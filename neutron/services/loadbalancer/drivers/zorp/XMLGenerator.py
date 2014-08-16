import xml.etree.ElementTree as ET

class XMLGenerator:
    def __init__(self, location="/tmp/policy.xml"):
        self.xml_location = location
        self.tree = ET.parse(self.xml_location)
        self.root = self.tree.getroot()

    def write_out_xml(self):
        self.tree.write(self.xml_location)

    def add_vip_to_pool(self, name, address, vip_id, pool_id, connection_limit, port_id, protocol, protocol_port, session_persistence):
        pool = self.find_pool_by_id(pool_id)
        child = ET.SubElement(pool, "vip", {"name" : name, "connection_limit" : "%s" % connection_limit,  "id" : vip_id, "protocol_port" : "%s" % protocol_port, "protocol" : protocol, "session_persistence" : session_persistence, "address" : address})
        self.write_out_xml()

    def update_vip_in_pool(self, name, address, vip_id, pool_id, connection_limit, port_id, protocol, protocol_port, session_persistence):
        self.remove_vip_from_pool(vip_id, pool_id)
        self.add_vip_to_pool(name, address, vip_id, pool_id, connection_limit, port_id, protocol, protocol_port, session_persistence)

    def remove_vip_from_pool(self, vip_id, pool_id):
        pool = self.find_pool_by_id(pool_id)
        vip = self.find_vip_in_pool_by_id(vip_id, pool)
        pool.remove(vip)
        self.write_out_xml()

    def find_pool_by_id(self, pool_id):
        for pool in self.root.findall('pool'):
            if pool.attrib['pool_id'] == pool_id:
                return pool

    def find_member_in_pool_by_id(self, member_id, pool):
        for member in pool.findall('member'):
            if member.attrib['id'] == member_id:
                return member

    def find_vip_in_pool_by_id(self, vip_id, pool):
        for member in pool.findall('vip'):
            if member.attrib['id'] == vip_id:
                return member


    def create_pool(self, name, subnet, protocol, balancing_method, pool_id, description=""):
        child = ET.SubElement(self.root, "pool", {"name" : name, "subnet" : subnet, "protocol" : protocol, "balancing_method" : balancing_method, "pool_id" : pool_id, "description" : description})
        self.write_out_xml()

    def update_pool (self, name, subnet, protocol, balancing_method, pool_id, description=""):
        pool = self.find_pool_by_id(pool_id)
        pool.set('name', name)
        pool.set('subnet', subnet)
        pool.set('protocol', protocol)
        pool.set('balancing_method', balancing_method)
        pool.set('description', description)
        self.write_out_xml()

    def delete_pool (self, pool_id):
        self.root.remove(self.find_pool_by_id(pool_id))
        self.write_out_xml()

    def add_member_to_pool(self, member_id, pool_id, protocol_port, member_address):
        pool = self.find_pool_by_id(pool_id)
        child = ET.SubElement(pool, "member", {"id" : member_id, "protocol_port" : "%s" % protocol_port, "address" : member_address})
        self.write_out_xml()

    def update_member_in_pool(self, member_id, pool_id, protocol_port, member_address):
        self.delete_member_from_pool(member_id, pool_id)
        self.add_member_to_pool(member_id, pool_id, protocol_port, member_address)

    def delete_member_from_pool(self, member_id, pool_id):
        pool = self.find_pool_by_id(pool_id)
        member = self.find_member_in_pool_by_id(member_id, pool)
        pool.remove(member)
        self.write_out_xml()
