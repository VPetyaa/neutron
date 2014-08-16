import contextlib

import mock

from neutron.common import exceptions
from neutron.services.loadbalancer.drivers.zorp.XMLGenerator import *
from neutron.tests import base
import xml.etree.ElementTree as ET
import random, string
import os


def randomword(length):
   return ''.join(random.choice(string.lowercase) for i in range(length))


class TestXMLGenerator(base.BaseTestCase):
    def setUp(self):
        super(TestXMLGenerator, self).setUp()
        self.random_file_name = '/tmp/%s.xml' % randomword(8)
        f = open(self.random_file_name,'w')
        f.write('<policy />\n')
        f.close()
        self.generator = XMLGenerator(location = self.random_file_name)
        self.pool_models = []
        self.pool_attr_list = ['name',
                               'subnet',
                               'protocol',
                               'balancing_method',
                               'pool_id',
                               'description']
        self.member_attr_list = ['id',
                                 'protocol_port',
                                 'address']
        self.vip_attr_list = ['name',
                              'address',
                              'id',
                              'connection_limit',
                              'protocol',
                              'protocol_port',
                              'session_persistence']


    def tearDown(self):
        super(TestXMLGenerator, self).tearDown()
        try:
            os.remove(self.random_file_name)
        except:
            pass


    def check_consistency(self):
        pools = self.generator.root.findall("pool")
        self.assertEquals(len(pools), len(self.pool_models))
        
        for pool_model in self.pool_models:
            self.is_pool_match_model(
                self.generator.root.find("pool[@pool_id='%s']" % pool_model['pool_id']),
                pool_model)

    
    def is_pool_match_model(self, pool, pool_model):
        for i in self.pool_attr_list:
            self.assertEquals(pool.get(i), pool_model[i])
        members = pool.findall('member')
        self.assertEquals(len(members), len(pool_model['members']))
        print members

        for model_member in pool_model['members']:
            real_member = pool.find("member[@id='%s']" % model_member['id'])
            for i in self.member_attr_list:
                self.assertEquals(real_member.get(i), model_member[i])

        vip = pool.find('vip')
        self.assertEquals((vip == None and pool_model['vip'] == None) or (vip != None and pool_model['vip'] != None), True)
        if vip is not None:
            for i in self.vip_attr_list:
                self.assertEquals(vip.get(i), pool_model['vip'][i])

 
    def generate_dummy_pool(self, pid, name, subnet='10.1.0.0', prot='tcp', balancing_method='round_robin', desc='desc'):
        pool_model = {'name': name,
                    'subnet' : subnet,
                    'protocol': prot,
                    'balancing_method' : balancing_method,
                    'pool_id' : pid,
                    'description': desc,
                    'members' : [],
                    'vip' : None}

        self.generator.create_pool(pool_model['name'],
                                    pool_model['subnet'],
                                    pool_model['protocol'],
                                    pool_model['balancing_method'],
                                    pool_model['pool_id'],
                                    pool_model['description'])
        self.pool_models.append(pool_model)
        return pool_model


    def generate_dummy_member(self, pid, member_id, protocol_port=80, address='10.10.0.1'):
        member_model = {'pool_id' : pid,
                        'id' : member_id,
                        'protocol_port' : '%s' % protocol_port,
                        'address' : address}
        self.generator.add_member_to_pool(member_id, pid, protocol_port, address)
        return member_model


    def update_pool(self, pool_model):
        self.generator.update_pool(pool_model['name'],
                                   pool_model['subnet'],
                                   pool_model['protocol'],
                                   pool_model['balancing_method'],
                                   pool_model['pool_id'],
                                   pool_model['description'])
        
        for model in self.pool_models:
            if model['pool_id'] == pool_model['pool_id']:
                self.pool_models.remove(model)
        self.pool_models.append(pool_model)


    def delete_pool(self, pool_model):
        self.generator.delete_pool(pool_model['pool_id'])
        self.pool_models.remove(pool_model)


    def update_member(self, member_model):
        self.generator.update_member_in_pool(member_model['id'],
                                             member_model['pool_id'],
                                             member_model['protocol_port'],
                                             member_model['address'])


    def delete_member(self, member_model):
        self.generator.delete_member_from_pool(member_model['id'], member_model['pool_id'])


    def generate_vip(self, pool_id):
        vip_model = { 'name' : 'tmp_name',
                        'address': '1.1.1.1',
                        'id' : '1',
                        'pool_id' : pool_id,
                        'connection_limit' : '5',
                        'port_id' : '1',
                        'protocol' : 'tcp',
                        'protocol_port' : '80',
                        'session_persistence' : 'source_ip'}

        for pool in self.pool_models:
            if pool['pool_id'] == pool_id:
                pool['vip'] = vip_model

        self.generator.add_vip_to_pool(vip_model['name'],
                                       vip_model['address'],
                                       vip_model['id'],
                                       vip_model['pool_id'],
                                       vip_model['connection_limit'],
                                       vip_model['port_id'],
                                       vip_model['protocol'],
                                       vip_model['protocol_port'],
                                       vip_model['session_persistence'])
        return vip_model


    def delete_vip(self, vip):
        self.generator.remove_vip_from_pool(vip['id'], vip['pool_id'])
        
        for pool in self.pool_models:
            if pool['pool_id'] == vip['pool_id']:
                    pool['vip'] = None

        


    def test_pool_functions(self):
        pool_model = self.generate_dummy_pool('1', 'pool')

        self.check_consistency()

        pool_model2 = self.generate_dummy_pool('2', 'pool2')
        
        self.check_consistency()

        pool_model['balancing_method'] = 'source_ip'
        pool_model2['balancing_method'] = 'round_robin'
        self.update_pool(pool_model)
        self.update_pool(pool_model2)
        
        self.check_consistency()

        self.delete_pool(pool_model)
        self.check_consistency()

        self.delete_pool(pool_model2)
        self.check_consistency()


    def test_member_functions(self):
        pool_model = self.generate_dummy_pool('1', 'pool')
        pool_model2 = self.generate_dummy_pool('2', 'pool2')
       
        member_first_pool = []
        member_first_pool.append(self.generate_dummy_member('1', 'member1'))
        member_first_pool.append(self.generate_dummy_member('1', 'member2'))
        member_first_pool.append(self.generate_dummy_member('1', 'member3'))
        member_first_pool.append(self.generate_dummy_member('1', 'member4'))

        member_snd_pool = []
        member_snd_pool.append(self.generate_dummy_member('2', '2member1'))
        member_snd_pool.append(self.generate_dummy_member('2', '2member2'))
        member_snd_pool.append(self.generate_dummy_member('2', '2member3'))

        pool_model['members'] = member_first_pool
        pool_model2['members'] = member_snd_pool

        self.check_consistency()

        member_first_pool[2]['name'] = 'new_name'
        self.update_member(member_first_pool[2])

        pool_model['balancing_method'] = 'source_ip'
        pool_model2['balancing_method'] = 'round_robin'
        self.update_pool(pool_model)
        self.update_pool(pool_model2)
        
        member_snd_pool[0]['address'] = '1.1.1.1'
        self.update_member(member_snd_pool[0])
        
        self.check_consistency()

        self.delete_member(member_snd_pool[2])
        del member_snd_pool[2]

        self.check_consistency()

        self.delete_member(member_snd_pool[1])
        del member_snd_pool[1]
        self.delete_member(member_snd_pool[0])
        del member_snd_pool[0]

        self.check_consistency()

        self.delete_pool(pool_model2)
        self.check_consistency()

    def test_vip_functions(self):
        
        pool_model = self.generate_dummy_pool('1', 'pool')
        pool_model2 = self.generate_dummy_pool('2', 'pool2')

        fst_vip = self.generate_vip('1')
        self.check_consistency()
        
        self.generate_vip('2')
        self.check_consistency()
        
        self.delete_vip(fst_vip)
        self.check_consistency()

