import base64
import copy
import httplib
import netaddr
import threading
import time
import commands


import eventlet
eventlet.monkey_patch(thread=True)

from oslo.config import cfg
from six.moves import queue as Queue

from neutron.api.v2 import attributes
from neutron.common import log as call_log
from neutron import context
from neutron.db.loadbalancer import loadbalancer_db
from neutron.extensions import loadbalancer
from neutron.openstack.common import excutils
from neutron.openstack.common import jsonutils as json
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.services.loadbalancer.drivers import abstract_driver
from neutron.services.loadbalancer.drivers.zorp import exceptions as r_exc
from neutron.services.loadbalancer.drivers.zorp.generator import XMLGenerator
from neutron.services.loadbalancer.drivers.zorp.PolicyPyFromXMLGenerator import PolicyPyFromXMLGenerator


LOG = logging.getLogger(__name__)

VIPS_RESOURCE = 'vips'
VIP_RESOURCE = 'vip'
POOLS_RESOURCE = 'pools'
POOL_RESOURCE = 'pool'
POOLMEMBERS_RESOURCE = 'members'
POOLMEMBER_RESOURCE = 'member'
MONITORS_RESOURCE = 'healthmonitors'
MONITOR_RESOURCE = 'healthmonitor'
POOLSTATS_RESOURCE = 'statistics'
PROV_SEGMT_ID = 'provider:segmentation_id'
PROV_NET_TYPE = 'provider:network_type'
DRIVER_NAME = 'zorp_driver'

class LoadBalancerDriver(abstract_driver.LoadBalancerAbstractDriver):

    """Zorp lbaas driver."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.generator = XMLGenerator()
        self.policy_py_generator = PolicyPyFromXMLGenerator(policy_xml='/tmp/policy.xml', policy_py='/tmp/policy.py')

    def get_service_type(self):
        return "Zorp Load Balancer"

    def start_zorp(self):
        commands.getoutput('sudo zorpctl restart')

    def get_type (self):
        return "Zorp LBaaS"

    def get_version (self):
        return "1.0"

    def create_vip(self, context, vip):
        status = constants.ACTIVE
        self.generator.add_vip_to_pool(vip['name'], self.plugin._core_plugin.get_subnet(context, vip['subnet_id'])['cidr'], vip['address'], vip['id'], vip['pool_id'], vip['connection_limit'], vip['port_id'], vip['protocol'], vip['protocol_port'], vip['session_persistence']['type'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin.update_status(context, loadbalancer_db.Vip, vip["id"], status)

    def update_vip(self, context, old_vip, vip):
        status = constants.ACTIVE
        self.generator.update_vip_in_pool(vip['name'], self.plugin._core_plugin.get_subnet(context, vip['subnet_id'])['cidr'], vip['address'], vip['id'], vip['pool_id'], vip['connection_limit'], vip['port_id'], vip['protocol'], vip['protocol_port'], vip['session_persistence']['type'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin.update_status(context, loadbalancer_db.Vip, old_vip["id"], status)

    def delete_vip(self, context, vip):
        self.generator.remove_vip_from_pool(vip['id'], vip['pool_id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin._delete_db_vip(context, vip['id'])

    def _get_network_info(self, context, entity):
        network_info = {}
        subnet_id = entity['subnet_id']
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)
        network_id = subnet['network_id']
        network = self.plugin._core_plugin.get_network(context, network_id)
        network_info['network_id'] = network_id
        network_info['subnet_id'] = subnet_id
        if PROV_NET_TYPE in network:
            network_info['network_type'] = network[PROV_NET_TYPE]
        if PROV_SEGMT_ID in network:
            network_info['segmentation_id'] = network[PROV_SEGMT_ID]
        return network_info

    def _get_vip_network_info(self, context, vip):
        network_info = self._get_network_info(context, vip)
        network_info['port_id'] = vip['port_id']
        return network_info

    def _get_pool_network_info(self, context, pool):
        return self._get_network_info(context, pool)

    def _get_pools_on_subnet(self, context, tenant_id, subnet_id):
        filter_dict = {'subnet_id': [subnet_id], 'tenant_id': [tenant_id]}
        return self.plugin.get_pools(context, filters=filter_dict)

    def _get_snatport_for_subnet(self, context, tenant_id, subnet_id):
        device_id = '_lb-snatport-' + subnet_id
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)
        network_id = subnet['network_id']
        msg = (_("Filtering ports based on network_id=%(network_id)s, "
                 "tenant_id=%(tenant_id)s, device_id=%(device_id)s") %
               {'network_id': network_id,
                'tenant_id': tenant_id,
                'device_id': device_id})
        LOG.debug(msg)
        filter_dict = {
            'network_id': [network_id],
            'tenant_id': [tenant_id],
            'device_id': [device_id],
            'device-owner': [DRIVER_NAME]
        }
        ports = self.plugin._core_plugin.get_ports(context,
                                                   filters=filter_dict)
        if ports:
            msg = _("Found an existing SNAT port for subnet %s") % subnet_id
            LOG.info(msg)
            return ports[0]
        msg = _("Found no SNAT ports for subnet %s") % subnet_id
        LOG.info(msg)

    def _create_snatport_for_subnet(self, context, tenant_id, subnet_id,
                                    ip_address):
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)
        fixed_ip = {'subnet_id': subnet['id']}
        if ip_address and ip_address != attributes.ATTR_NOT_SPECIFIED:
            fixed_ip['ip_address'] = ip_address
        port_data = {
            'tenant_id': tenant_id,
            'name': '_lb-snatport-' + subnet_id,
            'network_id': subnet['network_id'],
            'mac_address': attributes.ATTR_NOT_SPECIFIED,
            'admin_state_up': False,
            'device_id': '_lb-snatport-' + subnet_id,
            'device_owner': DRIVER_NAME,
            'fixed_ips': [fixed_ip],
        }
        port = self.plugin._core_plugin.create_port(context,
                                                    {'port': port_data})
        msg = _("Created SNAT port: %s") % repr(port)
        LOG.info(msg)
        return port

    def _remove_snatport_for_subnet(self, context, tenant_id, subnet_id):
        port = self._get_snatport_for_subnet(context, tenant_id, subnet_id)
        if port:
            self.plugin._core_plugin.delete_port(context, port['id'])
            msg = _("Removed SNAT port: %s") % repr(port)
            LOG.info(msg)

    def _create_snatport_for_subnet_if_not_exists(self, context, tenant_id,
                                                  subnet_id, network_info):
        port = self._get_snatport_for_subnet(context, tenant_id, subnet_id)
        if not port:
            msg = _("No SNAT port found for subnet %s."
                    " Creating one...") % subnet_id
            LOG.info(msg)
            port = self._create_snatport_for_subnet(context, tenant_id,
                                                    subnet_id,
                                                    ip_address=None)
        network_info['port_id'] = port['id']
        network_info['snat_ip'] = port['fixed_ips'][0]['ip_address']
        msg = _("SNAT port: %s") % repr(port)
        LOG.info(msg)

    def _remove_snatport_for_subnet_if_not_used(self, context, tenant_id,
                                                subnet_id):
        pools = self._get_pools_on_subnet(context, tenant_id, subnet_id)
        if not pools:
            #No pools left on the old subnet.
            #We can remove the SNAT port/ipaddress
            self._remove_snatport_for_subnet(context, tenant_id, subnet_id)
            msg = _("Removing SNAT port for subnet %s "
                    "as this is the last pool using it...") % subnet_id
            LOG.info(msg)


    def create_pool(self, context, pool):
        status = constants.ACTIVE
        network_info = self._get_pool_network_info(context, pool)
        self._create_snatport_for_subnet_if_not_exists(context,
                                                pool['tenant_id'],
                                                pool['subnet_id'],
                                                network_info)
        import pdb; pdb.set_trace()
        self.generator.create_pool(pool['name'], self.plugin._core_plugin.get_subnet(context, pool['subnet_id'])['cidr'], pool['protocol'], pool['lb_method'], pool['id'], pool['description'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()
        self.plugin.update_status(context, loadbalancer_db.Pool, pool["id"], status)

    def update_pool (self, context, old_pool, pool):
        status = constants.ACTIVE
        self.generator.update_pool(pool['name'], self.plugin._core_plugin.get_subnet(context, pool['subnet_id'])['cidr'], pool['protocol'], pool['lb_method'], pool['id'], pool['description'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin.update_status(context, loadbalancer_db.Pool, old_pool["id"], status)

    def delete_pool (self, context, pool):
        self.generator.delete_pool(pool['id'])
        self.plugin._delete_db_pool(context, pool['id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()
        self._remove_snatport_for_subnet_if_not_used(context,
                                                    pool['tenant_id'],
                                                    pool['subnet_id'])


    def create_member (self, context, member):
        status = constants.ACTIVE
        self.generator.add_member_to_pool(member['id'], member['pool_id'], member['protocol_port'], member['tenant_id'], member['address'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin.update_status(context, loadbalancer_db.Member, member["id"], status)

    def update_member (self, context, old_member, member):
        status = constants.ACTIVE
        self.generator.update_member_in_pool(member['id'], member['pool_id'], member['protocol_port'], member['tenant_id'], member['address'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin.update_status(context, loadbalancer_db.Member, old_member["id"], status)

    def delete_member (self, context, member):
        self.generator.delete_member_from_pool(member['id'], member['pool_id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.start_zorp()

        self.plugin._delete_db_member(context, member['id'])

    def create_pool_health_monitor (self, context, health_monitor, pool_id):
        pass
    
    def update_pool_health_monitor (self, context, old_health_monitor, health_monitor, pool_id):
        pass

    def delete_pool_health_monitor (self, context, health_monitor, pool_id):
        pass


    def stats (self, context, pool_id):
        pass

