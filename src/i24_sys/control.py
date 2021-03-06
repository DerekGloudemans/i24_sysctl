import sys
import time
import configparser
import socket
import _pickle as pickle
import os
import sys
import json
import multiprocessing as mp

from i24_logger.log_writer import logger, catch_critical
logger.set_name("ClusterControl")


def dummyServer():
    try:
        socks = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_address = ("10.2.219.150",5993)
        socks.bind(server_address)
        
        # Listen for incoming connections
        socks.listen(1)
        
        # Wait for a connection
        connection, client_address = socks.accept()
    
        time.sleep(60)
        connection.close()
        
    except Exception as e:
        if type(e).__name__ == "KeyboardInterrupt":
            pass
        else:
            print(type(e).__name__)
            socks.shutdown(socket.SHUT_RDWR)
            socks.close()
            connection.close()
            raise Exception("Closed dummyServer Socket")
    
#pr = mp.Process(target = dummyServer,args = ())
#pr.start()

class ClusterControl:
    """
    The ClusterControl class controls the entire I24 system. It is itself controlled 
    by two means: 
        1. Static (per run) control configs which are modified once before running
        2. User-keystroke input commands
        
    Set in config:
        - which cameras are to be used
        - which cameras are managed by each state-level node
        - video time at which to start processing 
        - video time at which to end processing
    
    From this set of configs, the FederalControl module generates the necessary config
    files for each state-level system and sends these via tcp to the state-level manager.
    It is assumed that the state level management process is running on each machine
    
    Then, the system control generates the set of processes / inputs that should be run
    on each state-level system and sends these via an additional config file.
    
    Then, the start signal is sent to each state-level manager
    
    Key Commands:
        1.) soft shutdown
        2.) hard shutdown
        
    """
    
    @catch_critical()
    def __init__(self,run_config_file,process_config_directory):
        """
        :param run_config_file - (str) path to federal-level run config file
        """
        logger.set_name("ClusterControl")

        
        # parse config to get run settings
        cp = configparser.ConfigParser()
        cp.read(run_config_file)
        
        self.params = dict(cp["PARAMETERS"])
        self.params = dict([(key.upper(),self.params[key]) for key in self.params]) # make  parameter names all-uppercase
        
        self.servers = dict(cp["SERVERS"])
        self.servers = dict([(key,(self.servers[key].split(":")[0],int(self.servers[key].split(":")[1]))) for key in self.servers])
        
        # generate config files for each node
        self.configs = self.generate_configs(process_config_directory)
        
        # establish TCP connections with each state-level node (I am the client)
        # self.sockets is keyed by server names as specified in ClusterControl.config
        self.sockets = {}
        for server in self.servers.keys():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Connect the socket to the port where the server is listening
            server_address = (self.servers[server])
            sock.connect(server_address)
            self.sockets[server] = sock
        
        # define command dictionary with command handle,help description
        self.cmd = {
            "START"              :"start/restart all processes as needed based on Federal-level run config",
            "FINISH PROCESSING"  : "less urgent graceful shutdown",
            "SOFT STOP"          : "component-implemented graceful shutdown",
            "HARD STOP"          : "default state-level process termination"
            }
      
    @catch_critical()
    def sock_send(self,payload,server_name):
        msg = pickle.dumps(payload)
        self.sockets[server_name].sendall(msg)
        
    @catch_critical()
    def generate_configs(self,process_config_directory):
        """
        The current implementation assumes static camera-to-machine mapping, 
        and assumes that each state-level machine manages its own hardware devices 
        (camera-to-GPU mapping). Thus, this config consists of a list of processes
        and arguements to be run by the state-level manager. Note that it is expected that 
        self.params contains values for all of the $demarcated variable names in each JSON
        
        :param process_config_directory - (str) directory with a json-style set of processContainers for each server
        
        processContainer = {
            "mode"   : "subprocess" or "process",
            "command": "terminal call" or "key to function lookup",
            "timeout": 1,
            "args": [],
            "kwargs": {}, 
            "flags": [],
            "group": "ingest" or "tracking" or "postprocessing" or "archive",
            "description": "This process specifies 2 arguments, 0 keyword arguments and 0 flags at the Cluster Level. It expects 2 additional arguments and one additional keyword argument to be appended by ServerControl"
            }
        
        :returns None
        """
        
        configs = {}
        
        files = os.listdir(process_config_directory)
        files = [os.path.join(process_config_directory,file) for file in files]
        
        process_list = []
        for file in files:
            with open(file,"rb") as f:
                processes = json.load(f)
            
            for process in processes:
                
                # replace any $variables with variable values
                for item in process["args"]:
                    if item[0] == "$":
                        item = self.params[item[1:]]
                for item in process["kwargs"]:
                    if item[0] == "$":
                        item = self.params[item[1:]]
                for item in process["flags"]:
                    if item[0] == "$":
                        item = self.params[item[1:]]
                        
                # append to process_list
                process_list.append(process)
                
            server_name = file.split("/")[-1].split(":")[0]
            configs[server_name] = process_list
            
    
        logger.debug("Generated state-level run configs")
        return configs
    
    
    @catch_critical()
    def send_configs(self):
        for server in self.configs.keys():
            self.sock_send(self.configs[server],server)

        logger.debug("Sent run configs to all active ServerControl modules")
        
        

        
    def send_message(self,message,group = None):
        
        for server in self.servers:
            if group is None or self.servers[server]["group"] == group:
                self.sock_send(message, server)
        logger.debug("Sent command {} to all active ServerControl modules".format(message))
    
        
    @catch_critical()
    def main(self):
        print("Press ctrl+C to enter a commmand")
        while True:
            try:
                pass
            except KeyboardInterrupt:
                inp = input("Enter command or press (h) for list of valid commands: ")
                
                inp = inp.split(",")
                group = None
                if len(inp) > 1:
                    group = inp[1]
                inp = inp[0]
                print(inp)
                
                if inp in ["h","H","help","HELP"]:
                    print("Valid commands:")
                    for key in self.cmd:
                        print("{} : {}".format(key,self.cmd[key]))

                
                elif inp in self.cmd.keys():
                    self.send_message(inp,group = group)
                
                else:
                    print("Invalid command. Re-entering waiting loop...")
                    time.sleep(1)
                    print("Press ctrl+C to enter a commmand")
                    



class ServerControl:
    """
    ServerControl has a few main functions. 
    1. Continually open socket (server side) that listens for commands from ClusterControl
    2. Start and maintain a list of subprocesses and processes
    3. Monitor these processes and restart them as necessary
    4. Log any status changes
    """
    
    
    def __init__(self,sock_port = 5999):
        logger.set_name("ServerControl")

        self.TCP_dict = {} # which function to run for each TCP message
        
        self.process_list = []
        
        
        # create socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        server_address = (local_ip,sock_port)
        self.sock.bind(server_address)
        self.sock.listen(1)
        
        
        # Wait for a connection
        try:
            self.connection, self.client_address = self.sock.accept()
            logger.debug("ServerControl connected to ClusterControl")
            
            # wait for initial process commands
            payload = self.sock.recv(4096)
            self.process_list = pickle.loads(payload)
        
            # start processes
            for proc in self.process_list:
                if proc["mode"] == "subprocess":
                    pid = self.start_subproc(proc)
                elif proc["mode"] == "process":
                    pid = self.start_proc(proc)
                proc["pid"] = pid
        
        except:
            self.cleanup()
        
    
    def start_proc(self): pass
    
    def start_subproc(self): pass
        
    def get_proc_status(self): pass
    
    def cleanup (self): 
        """ Close socket, log shutdown, etc"""
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()
        self.connection.close()

        
    def send_signal(self): pass
    

    def main(self):
        pass

def get_server():
    s = ServerControl()
    

if __name__ == "__main__":
    
    pr = mp.Process(target = get_server,args = ())
    pr.start()
    
    time.sleep(1)
    
    run_config_file = "/home/derek/Documents/i24/i24_sysctl/config/ClusterControl.config"
    process_config_directory = "/home/derek/Documents/i24/i24_sysctl/config/servers"                  
    c = ClusterControl(run_config_file, process_config_directory)
    c.main()