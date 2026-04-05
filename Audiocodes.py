import pathlib
import logging
import sys
import requests
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logger.setLevel(level=logging.CRITICAL)


class API:

    header = "FORMAT Index = TrunkGroupNum, FirstTrunkId, FirstBChannel, LastBChannel, FirstPhoneNumber, ProfileName, LastTrunkId, Module;"
    def read_env(self, keyfile: pathlib.Path) -> dict:

        logger.debug(f"Attempting to read audiocodes credentails file: {keyfile}")

        creds = { }

        try:
            with open(file=keyfile, mode="r") as f:
                for line in f.readlines():
                    s = line.split("=")
                    if s[0].lower() == 'username': creds['username'] = s[1].strip(); continue
                    if s[0].lower() == 'password': creds['password'] = s[1].strip(); continue
        except FileNotFoundError as e:
            logger.critical(f"Could not find the provided creds file: {e}")
            sys.exit(1)  
        except Exception as e:
            logger.critical(f"Unexpcted error when reading creds file: {e}")
            sys.exit(1)  

        if not creds:
            logger.critical("Creds were null after reading file.")
            sys.exit(1)  
        
        logger.debug("Credentials read successfully.")
        return creds
    
    
    def test_login(self) -> bool:

        logger.debug(f"Attempting to test login to {self.ip}")

        try:
            res = requests.get(url=self.base_url, verify=False, auth=(self.creds['username'], self.creds['password']))
        except Exception as e:
            logger.critical(f"Could not log into the ATA this way! Reason: \n{e}")
            sys.exit(1)

        if not self.validate_http(res):
            return False
        return True
    
    
    def validate_http(self, res: requests.Response) -> bool:
        logger.debug("Attempting to validate an HTTP response code.")

        match res.status_code:
            case 200:
                logger.debug(f"HTTP response: {res.status_code} - OK")
                return True
            case 404:
                logger.error("HTTP response: {res.status_code} - NOT FOUND")
                return False
            case _:
                logger.error(f"HTTP response: {res.status_code} - UNACCOUNTED FOR")
                return False
        

    def __init__(self, key_file: pathlib.Path, audiociodes_ip="", verbosity=40) -> None:

        if verbosity != 40: 
            try:
                logger.setLevel(level=verbosity)
            except Exception as e:
                logger.warning(f"Tried to set the logger level to {verbosity} but failed. Reverting to default of 40")
                logger.setLevel(level=logging.ERROR)

        self.ip         = audiociodes_ip
        self.creds      = self.read_env(key_file)
        self.base_url   = f"https://{self.ip}/api/v1"

     
    def set_ip(self, i: str): self.ip = i
    def set_base_url(self, i: str): self.base_url = f"https://{i}/api/v1"

    def fetch_ini(self) -> requests.Response.text:

        logger.debug("Attemting to fetch an INI file.")

        url = f"{self.base_url}/files/ini"

        res = requests.get(url=url, verify=False, auth=(self.creds['username'], self.creds['password']))

        if not self.validate_http(res):
            sys.exit(1)

        logger.debug("INI file successfully returned.")
        return res.text
    

    def extract_ini_trunk_groups(self, ini: str) -> str:
        logger.debug("Attempting to prepare the TrunkGroup section of the provided INI file.")

        TrunkGroups = ""

        try:
            res = re.findall(pattern=r"(TrunkGroup\s\d.*)", string=ini, flags=re.MULTILINE)
            TrunkGroups = "\n".join(res)

            if not TrunkGroups:
                logger.critical("Failed to extract the TrunkGroup from the INI file.")
                sys.exit(1)
        except Exception as e:
            logger.critical(f"Exception thrown: {e}")
            sys.exit(1)

        logger.debug("Successfully parsed the INI file for a TrunkGroup section")

        final = "[ TrunkGroup ]\n\n" + self.header + "\n" + TrunkGroups + "\n\n" + r"[ \TrunkGroup ]"
  
        return final
    
    def get_product_details(self) -> requests.Response.json:

        logger.debug("Attempting to fetch the product details.")

        url = f"{self.base_url}/status"

        res = requests.get(url=url, verify=False, auth=(self.creds['username'], self.creds['password']))
      
        if not self.validate_http(res):
            logger.critical("Product detail method critical failure.")
            sys.exit(1)
        
        logger.debug("Successfully fetched the product details.")

        return res.json()