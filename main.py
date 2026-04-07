#!/usr/bin/env python3

"""

rsyslog will execute this script whenever a log message matches one of two regex filters:

    - A message containing re`/.*AutoUpdate: HTTP Download completed.*)/`
        : these messages are sent when zoom successfully pushes a config to the gateway

    - A message containing re`/(.*Activity Log: TrunkGroupTable row [0-9]{0,3} \\- 'TrunkGroupId' was changed to '[0-9]{0,2}'.*)/`
        : these messages are sent when a trunk group id is modified

    rsyslog will send the log message to this script via stdin. after 30 seconds of no messages, rsyslog
    will kill this PID.
"""


import Audiocodes
import zoom
import logging
from logging.handlers import RotatingFileHandler
import sys
import re
import datetime
import pathlib
import os


path = "."
if not pathlib.Path(os.path.join(path, "configs")).exists():
    logging.debug("Directory config/ didn't exist, creating now.")
    os.mkdir("configs/")

if not pathlib.Path(os.path.join(path, "logs")).exists():
    logging.debug("Directory logs/ didn't exist, creating now.")
    os.mkdir("logs/")

logger = logging.getLogger(__name__)
logging.basicConfig(
    handlers=[ RotatingFileHandler(os.path.join(path, "logs/zoomsync.log"), maxBytes=100000, backupCount=10) ],
    filemode="a+", 
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
    datefmt='%Y-%m-%dT%H:%M:%S'
)

logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("charset_normalizer").setLevel(logging.WARNING)


logger.info("PROG INIT")


zoom_env = os.path.join(path, ".env_zoom")
audiocodes_env = os.path.join(path, ".env_audiocodes")

zoom_client = zoom.zoom_client(
    warn=True,
    key_file=zoom_env,
    verbosity=logging.DEBUG
)

audiocodes = Audiocodes.API(
    verbosity=10,
    key_file=audiocodes_env,
)


def find_existing_provision_template(m: str, z: zoom.zoom_client) -> str:

    logger.debug("Attempting to see if this provisioning template exists.")

    next_page_token = ""
    template_id = ""

    while True:
        res = z.list_provision_templates(page_size=30, next_page_token=next_page_token)

        next_page_token = res["next_page_token"]
        for template in res["provision_templates"]:
            if m in template["description"]:
                logger.debug(f"A provisioning template with the mac {m} was found with the name {template['name']}.")
                logger.debug("We will skip creating a provisioning template and modify the existing one.")
                template_id = template["id"]
                return template_id
        
        logger.debug(f"There was no existing provisioning template with the mac {m} in the description, will create one.")
        return template_id



def get_device_from_zoom(m: str, z: zoom.zoom_client) -> dict:

    logger.debug("Attempting to get device details for this MP1288 from Zoom.")

    device_details = {
        "name" : None,
        "id":   None,
        "template_id": None
    }

    device = z.list_devices(type="assigned", keyword=m)

    if device["total_records"] == 1:
        logger.debug("Successfully found this device in Zoom.")
        try:
            device_details["name"] = device["devices"][0]["display_name"]
            device_details["id"] = device["devices"][0]["id"]
            device_details["template_id"] = device["devices"][0]["provision_template_id"]
        except Exception as e:
            logger.critical(f"Could not extract Zoom device details. Reason: {e}")
            print("Critical error, see logs.")
            sys.exit(1)
    else:
        logger.critical(f"Unable to locate this device in Zoom.")
        logger.critical(f"Total records: {device['total_records']}.")
        print(f"Could not find {m} in Zoom. Check logs for details.")
        sys.exit(1)            

    return device_details


def diff_ini_files(m: str, t: str) -> str:
    """
        when a port is moved on the ata (from index X to index Y) to INI file will
        reflect this device on both ports because the device was never deleted, just moved.

        this function will attept to allow users to `move` a device without first deleting it and then
        recreating it on the desired port, by diffing an old version of the INI file with the new one.
    """
    logger.debug("Attempting to see if this update will cause port duplication issues.")

    found = False

    for file in pathlib.Path(os.path.join(path, "configs")).iterdir():

        if file.name == f"{m}.old":
            logger.debug(f"File {m}.old was found, using this to compare the new update with the last update.")
            found = True

            with open(file=os.path.join(path, f"configs/{m}.old"), mode="r") as f:

                content = f.read()

                reg_ids = re.findall(pattern=r"\d{20}", string=t, flags=re.MULTILINE)

                seen = set()
                duplicates = [x for x in reg_ids if x in seen or seen.add(x)]

                # device has not moved ports, the config will be updated as normal
                if not duplicates: 
                    logger.debug("This update likely wasn't a port update. There were no duplicate reg id's in the INI.")
                    return t

                logger.debug(f"This update was likely a port update, found {len(duplicates)} duplicate reg id's.")
                for d in duplicates:
                    logger.debug(f"Reg ID is a duplicate: {d}")

                    pattern = r"(.*" + d + ".*)"

                    old_line = re.search(pattern=pattern, string=content, flags=re.MULTILINE)
                    
                    t = t.replace(old_line.group(0), "")
                    logger.debug(f"Deleted the old line: {old_line.group(0)}.")
                return t
    
    if not found:
        logger.warning("This script has been run for the first time. Beware of sync issues!")

        return t
       




for line in sys.stdin:

    """
    stdin will remain open until terminated. rsyslog will terminate this loop after 30 seconds of no messages
    """
    
    logger.info("  ")

    msg = line.strip()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # # # 
    hostname = re.search(pattern=r"\s\w+\-.*voice\.umn\.edu", string=msg)

    if not hostname:
        logger.error("Failed to extract the mac hostname from the rsyslog message.")
        continue
    hostname = hostname.group(0).strip()

    logger.debug(f"Extracted hostname: {hostname}")
    # # # 

    audiocodes.set_ip(hostname)
    audiocodes.set_base_url(hostname)

    if not audiocodes.test_login():
        logger.error("Failed to log into the audiocodes with the FQND provided by rsyslog")
        continue
    
    product_details = audiocodes.get_product_details()
    mac_address = product_details['macAddress'].upper()

    logger.debug(f"Extracted mac address: {mac_address}")

    trunk_groups = audiocodes.extract_ini_trunk_groups(ini=audiocodes.fetch_ini())
        
    # If the phone port was updated, delete the old port otherwise there will be duplicates   
    trunk_groups = diff_ini_files(m=mac_address, t=trunk_groups)

    # Check to see if this provisioning template exists
    template_id = find_existing_provision_template(m=mac_address, z=zoom_client)

    # Check if this MP1288 can be found in Zoom
    device_details = get_device_from_zoom(m=mac_address, z=zoom_client)

    body = {
        "name" : f"MP1288 Provision Template - {device_details['name']}",
        "description" : f"This template was generated programmatically on {timestamp}. The MAC in the description is needed for the script: {mac_address}.",
        "content" : trunk_groups
    }

    with open(file=os.path.join(path, f"configs/{mac_address}.old"), mode="w+", newline="") as old:
        old.write(trunk_groups)
        
    # Template doesn't exist
    if not template_id:
        
        logger.debug("Attempting to create a provision template in Zoom.")

        res = zoom_client.add_provision_template(body=body)
        try:
            logger.debug("Successfully created a provision template.")
            template_id = res["id"]
        except Exception as e:
            logger.critical(f"Failed to get the provosion template ID: {e}")
            print("Critical error, see logs.")
            sys.exit(1)


    # Template exists, update it
    else:

        logger.debug("Attempting to update a provision template in Zoom.")

        res = zoom_client.update_provision_template(template_id=template_id, body=body)
        try:
            logger.debug("Successfully updated a provision template.")
        except Exception as e:
            logger.critical("Failed to get the provision template ID.")
            print("Critical error, see logs.")
            sys.exit(1)
    
    # Is the tempate bound to the device?
    if device_details["template_id"] != template_id:

        logger.debug(f"The Audiocodes doesn't appear to have a provision template assigned to it. Assigning it {template_id}.")

        body = {"provision_template_id" : template_id}

        res = zoom_client.update_device(device_id=device_details["id"], body=body)
        if res.status_code != 204:
            logger.critical(f"Zoom replied with {res.status_code} when attempting to assign this provision template to the device.")
            print("Critical error, see logs.")
            sys.exit(1)
        
        logger.debug("Successfully bound this template ID to the device.")
    
    else:
        logger.debug("This device has the correct provision template bound to it.")

    

logger.info("Completed with no errors.")