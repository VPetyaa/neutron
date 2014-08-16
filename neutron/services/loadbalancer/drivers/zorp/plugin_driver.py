from neutron.services.loadbalancer.drivers.common import agent_driver_base
from neutron.services.loadbalancer.drivers.zorp import driver


class ZorpOnHostPluginDriver(agent_driver_base.AgentDriverBase):
    device_driver = driver.DRIVER_NAME
