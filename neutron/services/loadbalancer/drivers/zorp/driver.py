import os
import shutil
import socket
import commands

import netaddr
from oslo.config import cfg

from neutron.agent.common import config
from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils
from neutron.common import exceptions
from neutron.common import utils as n_utils
from neutron.openstack.common import excutils
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.plugins.common import constants
from neutron.services.loadbalancer.agent import agent_device_driver
from neutron.services.loadbalancer import constants as lb_const

from neutron.services.loadbalancer.drivers.zorp.XMLGenerator import XMLGenerator
from neutron.services.loadbalancer.drivers.zorp.PolicyPyFromXMLGenerator import PolicyPyFromXMLGenerator

LOG = logging.getLogger(__name__)
NS_PREFIX = 'qlbaas-'
DRIVER_NAME = 'zorp_ns'

STATE_PATH_DEFAULT = '$state_path/lbaas'
USER_GROUP_DEFAULT = 'nogroup'
OPTS = [
    cfg.StrOpt(
        'loadbalancer_state_path',
        default=STATE_PATH_DEFAULT,
        help=_('Location to store config and state files'),
        deprecated_opts=[cfg.DeprecatedOpt('loadbalancer_state_path')],
    ),
    cfg.StrOpt(
        'user_group',
        default=USER_GROUP_DEFAULT,
        help=_('The user group'),
        deprecated_opts=[cfg.DeprecatedOpt('user_group')],
    ),
    cfg.IntOpt(
        'send_gratuitous_arp',
        default=3,
        help=_('When delete and re-add the same vip, send this many '
               'gratuitous ARPs to flush the ARP cache in the Router. '
               'Set it below or equal to 0 to disable this feature.'),
    )
]
cfg.CONF.register_opts(OPTS, 'zorp')


class ZorpNSDriver(agent_device_driver.AgentDeviceDriver):
    def __init__(self, conf, plugin_rpc):
        self.conf = conf
        self.root_helper = config.get_root_helper(conf)
        self.state_path = conf.zorp.loadbalancer_state_path
        try:
            vif_driver = importutils.import_object(conf.interface_driver, conf)
        except ImportError:
            with excutils.save_and_reraise_exception():
                msg = (_('Error importing interface driver: %s')
                       % conf.zorp.interface_driver)
                LOG.error(msg)

        self.vif_driver = vif_driver
        self.plugin_rpc = plugin_rpc
        self.pool_to_port_id = {}

        # generators for zorp config
        # xml generator generates policy.xml for easy tracking of changes
        # policy.py generator policy.py and instances.conf for Zorp configuration
        self.xml_generator = XMLGenerator()
        # TODO: maybe loadbalancer_state_path should be used
        self.policy_py_generator = PolicyPyFromXMLGenerator(
            policy_xml='/tmp/policy.xml',
            policy_py='/tmp/policy.py',
            instances_conf='/tmp/instances.conf'
        )

    @classmethod
    def get_name(cls):
        return DRIVER_NAME

    def create(self, logical_config):
        pool_id = logical_config['pool']['id']
        namespace = get_ns_name(pool_id)

        self._plug(namespace, logical_config['vip']['port'])
        self._spawn(logical_config)

    def update(self, logical_config):
        pool_id = logical_config['pool']['id']

        self._spawn(logical_config)

    def _spawn(self, logical_config):
        pool_id = logical_config['pool']['id']
        namespace = get_ns_name(pool_id)

        # (re)start zorp in the lbaas namespace
        commands.getoutput('sudo ip netns exec %s zorpctl restart instance_%s' % (namespace, pool_id.replace('-','_')))

        # pool-vip_port mapping
        self.pool_to_port_id[pool_id] = logical_config['vip']['port']['id']

    @n_utils.synchronized('zorp-driver')
    def undeploy_instance(self, pool_id, cleanup_namespace=False):
        namespace = get_ns_name(pool_id)
        ns = ip_lib.IPWrapper(self.root_helper, namespace)

        # stop zorp in the lbaas namespace
        commands.getoutput('sudo ip netns exec %s zorpctl stop instance_%s' % (namespace, pool_id.replace('-','_')))

        # unplug the ports
        if pool_id in self.pool_to_port_id:
            self._unplug(namespace, self.pool_to_port_id[pool_id])

        # delete all devices from namespace;
        # used when deleting orphans and port_id is not known for pool_id
        if cleanup_namespace:
            for device in ns.get_devices(exclude_loopback=True):
                self.vif_driver.unplug(device.name, namespace=namespace)

        ns.garbage_collect_namespace()

    def exists(self, pool_id):
        namespace = get_ns_name(pool_id)
        root_ns = ip_lib.IPWrapper(self.root_helper)

        socket_path = self._get_state_file_path(pool_id, 'sock', False)
        if root_ns.netns.exists(namespace) and os.path.exists(socket_path):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(socket_path)
                return True
            except socket.error:
                pass
        return False

    def get_stats(self, pool_id):
        LOG.warn(_('Stats socket not found for pool %s'), pool_id)
        return {}

    def _parse_stats(self, raw_stats):
        stat_lines = raw_stats.splitlines()
        if len(stat_lines) < 2:
            return []
        stat_names = [name.strip('# ') for name in stat_lines[0].split(',')]
        res_stats = []
        for raw_values in stat_lines[1:]:
            if not raw_values:
                continue
            stat_values = [value.strip() for value in raw_values.split(',')]
            res_stats.append(dict(zip(stat_names, stat_values)))

        return res_stats


    def _get_state_file_path(self, pool_id, kind, ensure_state_dir=True):
        """Returns the file name for a given kind of config file."""
        confs_dir = os.path.abspath(os.path.normpath(self.state_path))
        conf_dir = os.path.join(confs_dir, pool_id)
        if ensure_state_dir:
            if not os.path.isdir(conf_dir):
                os.makedirs(conf_dir, 0o755)
        return os.path.join(conf_dir, kind)

    def _plug(self, namespace, port, reuse_existing=True):
        self.plugin_rpc.plug_vip_port(port['id'])
        interface_name = self.vif_driver.get_device_name(Wrap(port))

        if ip_lib.device_exists(interface_name, self.root_helper, namespace):
            if not reuse_existing:
                raise exceptions.PreexistingDeviceFailure(
                    dev_name=interface_name
                )
        else:
            self.vif_driver.plug(
                port['network_id'],
                port['id'],
                interface_name,
                port['mac_address'],
                namespace=namespace
            )

        cidrs = [
            '%s/%s' % (ip['ip_address'],
                       netaddr.IPNetwork(ip['subnet']['cidr']).prefixlen)
            for ip in port['fixed_ips']
        ]
        self.vif_driver.init_l3(interface_name, cidrs, namespace=namespace)

        gw_ip = port['fixed_ips'][0]['subnet'].get('gateway_ip')

        if not gw_ip:
            host_routes = port['fixed_ips'][0]['subnet'].get('host_routes', [])
            for host_route in host_routes:
                if host_route['destination'] == "0.0.0.0/0":
                    gw_ip = host_route['nexthop']
                    break

        if gw_ip:
            cmd = ['route', 'add', 'default', 'gw', gw_ip]
            ip_wrapper = ip_lib.IPWrapper(self.root_helper,
                                          namespace=namespace)
            ip_wrapper.netns.execute(cmd, check_exit_code=False)
            # When delete and re-add the same vip, we need to
            # send gratuitous ARP to flush the ARP cache in the Router.
            gratuitous_arp = self.conf.zorp.send_gratuitous_arp
            if gratuitous_arp > 0:
                for ip in port['fixed_ips']:
                    cmd_arping = ['arping', '-U',
                                  '-I', interface_name,
                                  '-c', gratuitous_arp,
                                  ip['ip_address']]
                    ip_wrapper.netns.execute(cmd_arping, check_exit_code=False)

    def _unplug(self, namespace, port_id):
        port_stub = {'id': port_id}
        self.plugin_rpc.unplug_vip_port(port_id)
        interface_name = self.vif_driver.get_device_name(Wrap(port_stub))
        self.vif_driver.unplug(interface_name, namespace=namespace)

    @n_utils.synchronized('zorp-driver')
    def deploy_instance(self, logical_config):
        # for zorp vip, members and pool is needed to be able to start
        if (not logical_config or
                'vip' not in logical_config or
                'members' not in logical_config or
                (logical_config['vip']['status'] not in
                 constants.ACTIVE_PENDING_STATUSES) or
                not logical_config['vip']['admin_state_up'] or
                (logical_config['pool']['status'] not in
                 constants.ACTIVE_PENDING_STATUSES) or
                not logical_config['pool']['admin_state_up']):
            return

        if self.exists(logical_config['pool']['id']):
            self.update(logical_config)
        else:
            self.create(logical_config)

    def _refresh_device(self, pool_id):
        logical_config = self.plugin_rpc.get_logical_device(pool_id)
        self.deploy_instance(logical_config)

    def create_vip(self, vip):
        self.xml_generator.add_vip_to_pool(vip['name'], vip['address'], vip['id'], vip['pool_id'], vip['connection_limit'], vip['port_id'], vip['protocol'], vip['protocol_port'], vip['session_persistence']['type'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(vip['pool_id'])

    def update_vip(self, old_vip, vip):
        self.xml_generator.update_vip_in_pool(vip['name'], vip['address'], vip['id'], vip['pool_id'], vip['connection_limit'], vip['port_id'], vip['protocol'], vip['protocol_port'], vip['session_persistence']['type'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(vip['pool_id'])

    def delete_vip(self, vip):
        self.xml_generator.remove_vip_from_pool(vip['id'], vip['pool_id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self.undeploy_instance(vip['pool_id'])

    def create_pool(self, pool):
        # without a vip the pool is useless, so no need for policy.py
        #  or instances.conf generation
        # FIXME: find out how to get cidr form subnet_id
        self.xml_generator.create_pool(pool['name'], 'FIXME', pool['protocol'], pool['lb_method'], pool['id'], pool['description'])

    def update_pool(self, old_pool, pool):
        self.xml_generator.update_pool(pool['name'], 'FIXME', pool['protocol'], pool['lb_method'], pool['id'], pool['description'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(pool['id'])

    def delete_pool(self, pool):
        self.xml_generator.delete_pool(pool['id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        if self.exists(pool['id']):
            self.undeploy_instance(pool['id'])

    def create_member(self, member):
        self.xml_generator.add_member_to_pool(member['id'], member['pool_id'], member['protocol_port'], member['address'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(member['pool_id'])

    def update_member(self, old_member, member):
        self.xml_generator.update_member_in_pool(member['id'], member['pool_id'], member['protocol_port'], member['address'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(member['pool_id'])

    def delete_member(self, member):
        self.xml_generator.delete_member_from_pool(member['id'], member['pool_id'])
        self.policy_py_generator.generate_policy_py_to_file()
        self.policy_py_generator.generate_instances_conf_to_file()
        commands.getoutput('sudo mv /tmp/instances.conf /etc/zorp/instances.conf')
        self._refresh_device(member['pool_id'])

    def create_pool_health_monitor(self, health_monitor, pool_id):
        # Zorp does not implement health monitor so it is useless
        pass

    def update_pool_health_monitor(self, old_health_monitor, health_monitor,
                                   pool_id):
        # Zorp does not implement health monitor so it is useless
        pass

    def delete_pool_health_monitor(self, health_monitor, pool_id):
        # Zorp does not implement health monitor so it is useless
        pass

    def remove_orphans(self, known_pool_ids):
        # It is accepted by agent_manager.py
        raise NotImplementedError


class Wrap(object):
    """A light attribute wrapper for compatibility with the interface lib."""
    def __init__(self, d):
        self.__dict__.update(d)

    def __getitem__(self, key):
        return self.__dict__[key]


def get_ns_name(namespace_id):
    return NS_PREFIX + namespace_id

