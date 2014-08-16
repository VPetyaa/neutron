import xml.etree.ElementTree as ET

class PolicyPyFromXMLGenerator(object):

    def __init__(self, policy_xml='policy.xml', policy_py='policy.py', instances_conf='instances.conf'):
        self.last_rule_id = 0
        self.infile = policy_xml
        self.outfile = policy_py
        self.instances_conf_outfile = instances_conf
        self.tree = ET.parse(self.infile)
        self.root = self.tree.getroot()
        self.lb_method_chainer_mapping = {
            'ROUND_ROBIN'       : 'RoundRobinChainer',
            'SOURCE_IP'         : 'SourceIPBasedChainer',
            'LEAST_CONNECTIONS' : 'LeastConnectionChainer'}

    def generate_instances_conf_to_file(self):
        self.tree = ET.parse(self.infile)
        self.root = self.tree.getroot()
        instances_conf = open(self.instances_conf_outfile, 'w')
        instances_conf.write(self.get_instances_conf())
        instances_conf.close()

    def get_instances_conf(self):
        ret = ""
        for pool in self.xml_get_pools():
            ret = ret + "instance_%s --threads 1000 --stack-size 256 --process-mode safe-background --verbose 3 --log-spec '*.accounting:4' --log-tags  --uid zorp --gid zorp --fd-limit-min 256000 --policy %s -- --num-of-processes 1\n\n" % (pool.attrib["pool_id"].replace('-','_'), self.outfile)
        return ret

    def generate_policy_py_to_file(self):
        self.tree = ET.parse(self.infile)
        self.root = self.tree.getroot()
        policy_py = open(self.outfile, 'w')
        policy_py.write(self.get_policy_py())
        policy_py.close()

    def get_policy_py(self):
        ret = ''
        ret = ret + "%s\n\n" % self.get_imports()
        services = self.get_services_from_xml_pools(
            self.xml_get_pools())
        dispatchers = self.get_dispatchers_from_xml_pools(
            self.xml_get_pools())
        for i in range(len(services)):

            try:
                ret = ret + "\n\ndef instance_%s():\n\n" % services['names'][i].replace('-','_')
                if not services['services'][i] == "   \n\n" and not dispatchers[i] == "   \n\n":
                    ret = ret + "%s" % services['services'][i]
                    ret = ret + "%s" % dispatchers[i]
                else:
                    ret = ret + "   pass\n\n"
            except IndexError:
                pass

        return ret

    def get_next_rule_id(self):
        self.last_rule_id += 1
        return self.last_rule_id

    def get_imports(self):
        return """from Zorp.Core import  *
from Zorp.Plug import  *
from Zorp.Proxy import  *

from zones import *"""

    def xml_get_pools(self):
        return self.root.findall("./pool")

    def xml_get_pool_members(self, pool):
        return pool.findall("./member")

    def xml_get_pool_vip(self, pool):
        return pool.findall("./vip")

    def get_dispatchers_from_xml_pools(self, pools):
        ret = []
        for pool in pools:
            ret.append("   %s\n\n" % self.get_dispatcher_from_xml_pool(pool))
        return ret

    def get_dispatcher_from_xml_pool(self, pool):
        if not self.xml_get_pool_vip(pool):
            return ""
        ret = "Dispatcher("
        ret = ret + "%s, " % self.get_bindto_dbsockaddr_from_xml_vip(
            self.xml_get_pool_vip(pool)[0])
        ret = ret + "%s, " % self.get_service_for_rule_from_xml_pool(pool)
        ret = ret + "transparent=FALSE, backlog=255)"
        return ret

    def get_bindto_dbsockaddr_from_xml_vip(self, vip):
        ret = 'bindto=DBSockAddr(protocol=ZD_PROTO_TCP, '
        ret = ret + "sa=SockAddrInet('%s', %s))" % (vip.attrib['address'],
            vip.attrib['protocol_port'])
        return ret

    def get_rules_from_xml_pools(self, pools):
        ret = ''
        for pool in pools:
            ret = ret + "   %s\n\n" % self.get_rule_from_xml_pool(pool)
        return ret

    def get_rule_from_xml_pool(self, pool):
        if not self.xml_get_pool_vip(pool):
            return ""
        ret = "Rule("
        ret = ret + "%s, " % self.get_rule_id()
        ret = ret + "%s, " % self.get_dst_subnet_from_xml_vip(
            self.xml_get_pool_vip(pool)[0])
        ret = ret + "%s, " % self.get_dst_port_from_xml_vip(
            self.xml_get_pool_vip(pool)[0])
        ret = ret + "%s, " % self.get_src_subnet_from_xml_pool(pool)
        ret = ret + "%s, " % self.get_service_for_rule_from_xml_pool(pool)
        ret = ret + "proto=6)"
        return ret

    def get_rule_id(self):
        return "rule_id=%d" % self.get_next_rule_id()

    def get_dst_port_from_xml_vip(self, vip):
        return "dst_port=%s" % vip.attrib['protocol_port']

    def get_dst_subnet_from_xml_vip(self, vip):
        return "dst_subnet=('%s', )" % vip.attrib['address']

    def get_src_subnet_from_xml_pool(self, pool):
        return "src_subnet=('%s', )" % pool.attrib['subnet']

    def get_service_for_rule_from_xml_pool(self, pool):
        return "service='%s'" % pool.attrib["pool_id"]

    def get_services_from_xml_pools(self, pools):
        ret = {'services' : [], 'names' : []}
        for pool in pools:
            ret['services'].append("   %s\n\n" % self.get_service_from_xml_pool(pool))
            ret['names'].append("%s" % pool.attrib["pool_id"])
        return ret

    def get_service_from_xml_pool(self, pool):
        if not self.xml_get_pool_members(pool):
            return ""
        ret = "Service("
        ret = ret + "%s, " % self.get_service_name_from_xml_pool(pool)
        ret = ret + "%s, " % self.get_directed_router_from_xml_pool_members(
            self.xml_get_pool_members(pool))
        ret = ret + "%s, " % self.get_chainer_from_xml_pool(pool)
        ret = ret + "proxy_class=PlugProxy, max_instances=0, max_sessions=0, keepalive=Z_KEEPALIVE_NONE)"
        return ret

    def get_service_name_from_xml_pool(self, pool):
        return "name='%s'" % pool.attrib["pool_id"]

    def get_chainer_from_xml_pool(self, pool):
        ret = ""
        ret = ret + "chainer=%s" % self.get_chainer_type_from_xml_pool(pool)
        ret = ret + "(protocol=ZD_PROTO_AUTO, timeout_connect=30, timeout_state=60)"
        return ret

    def get_chainer_type_from_xml_pool(self, pool):
        return self.lb_method_chainer_mapping[pool.attrib['balancing_method']]

    def get_directed_router_from_xml_pool_members(self, members):
        ret = 'router=DirectedRouter(dest_addr=('
        for member in members:
            ret = ret + "%s, " % self.get_sockaddrinet_from_xml_pool_member(member)
        ret = ret + "))"
        return ret

    def get_sockaddrinet_from_xml_pool_member(self, member):
        ret = "SockAddrInet("
        ret = ret + "'%s'," % self.get_address_from_xml_pool_member(member)
        ret = ret + "%s)" % self.get_port_from_xml_pool_member(member)
        return ret

    def get_address_from_xml_pool_member(self, member):
        return member.attrib['address']

    def get_port_from_xml_pool_member(self, member):
        return member.attrib['protocol_port']
