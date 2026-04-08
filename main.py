#!/usr/bin/env python3

"""
this script is intended to be executed by the rsyslog `omprog` module.

rsyslog will execute this script whenever a log message matches one of two regex filters:

    - A message containing re`/.*AutoUpdate: HTTP Download completed.*)/`
        : these messages are sent when zoom successfully pushes a config to the gateway

    - A message containing re`/(.*Activity Log: TrunkGroupTable row [0-9]{0,3} \\- 'TrunkGroupId' was changed to '[0-9]{0,2}'.*)/`
        : these messages are sent when a trunk group id is modified

    rsyslog will send the log message to this script via stdin. after 30 seconds of no messages, rsyslog will kill this PID.
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


path = r""

if not pathlib.Path(os.path.join(path, "configs")).exists():
    logging.debug("Directory config/ didn't exist, creating now.")
    os.mkdir("configs/")

if not pathlib.Path(os.path.join(path, "logs")).exists():
    logging.debug("Directory logs/ didn't exist, creating now.")
    os.mkdir("logs/")

logger = logging.getLogger(__name__)
logging.basicConfig(
    handlers=[ RotatingFileHandler(
        filename=os.path.join(path, "logs/zoomsync.log"), 
        mode="a+",
        maxBytes=100000, 
        backupCount=10) 
    ],
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
    datefmt=r"%Y-%m-%dT%H:%M:%S"
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
    key_file=audiocodes_env,
    verbosity=logging.INFO
)


def find_existing_provision_template(m: str, z: zoom.zoom_client) -> str:

    logger.info("Attempting to see if this provisioning template exists.")

    next_page_token = ""
    template_id = ""

    while True:
        res = z.list_provision_templates(page_size=30, next_page_token=next_page_token)

        next_page_token = res["next_page_token"]
        for template in res["provision_templates"]:
            if m in template["description"]:
                logger.info(f"A provisioning template with the mac {m} was found with the name {template['name']}.")
                logger.info("We will skip creating a provisioning template and modify the existing one.")
                template_id = template["id"]
                return template_id
        
        logger.info(f"There was no existing provisioning template with the mac {m} in the description, will create one.")
        return template_id



def get_device_from_zoom(m: str, z: zoom.zoom_client) -> dict:

    logger.info("Attempting to get device details for this MP1288 from Zoom.")

    device_details = {
        "name" : None,
        "id":   None,
        "template_id": None
    }

    device = z.list_devices(type="assigned", keyword=m)

    if device["total_records"] == 1:
        logger.info("Successfully found this device in Zoom.")
        try:
            device_details["name"] = device["devices"][0]["display_name"]
            device_details["id"] = device["devices"][0]["id"]
            device_details["template_id"] = device["devices"][0]["provision_template_id"]
        except Exception as e:
            logger.critical(f"Could not extract Zoom device details. Reason: {e}")
            return { }
    else:
        logger.critical(f"Unable to locate this device in Zoom.")
        logger.critical(f"Total records: {device['total_records']}.")
        return { }           

    return device_details

       
def detect_and_correct_port_move(t: str, z: zoom.zoom_client, d: dict) -> str:
    """
        when a port is moved on the ata (from index X to index Y) to INI file will
        reflect this device on both ports because the device was never deleted. The provision template will 
        list the old port and the new port and thus keep the INI file out of sync.

        this function will attept to allow users to `move` a device without first deleting it and then
        recreating it on the desired port by comparing the ports ad positions in zoom with
        the INI file from the device and delete the incongruent line.
    """

    logger.info("Attempting to get the audiocodes device ports and positions.")

    logger.info("Attempting to get the device line keys from Zoom.")

    zoom_positions = z.get_device_line_keys(device_id=d)

    if not zoom_positions:
        logger.error("Failed to get the ports and positions from the audiocodes. See logs. Sync issues likely.")
        sys.stderr.write("Failed to get the ports and positions from the audiocodes. See logs. Sync issues likely.")
        sys.stderr.flush()
        return t
    
    logger.info("Successfully fetched the device ports and positions")

    zoom_positions = [ x["index"] for x in zoom_positions["positions"] ]

    # Parse out the index (zero indexed) from the Trunk Groups section of the INI file
    ini_positions = [ int(x) + 1 for x in re.findall(pattern=r"TrunkGroup\s(\d{0,3})\s.*", string=t, flags=re.MULTILINE) ]

    if not ini_positions:
        logger.error("Failed to parse out the port indicies from the INI file. Sync issues likely.")
        sys.stderr.write("Failed to parse out the port indicies from the INI file. Sync issues likely.")
        sys.stderr.flush()
        return t
    
    # Return from left what is not found in right
    diff = list(set(ini_positions) - set(zoom_positions)) 

    if diff:
        logger.info(f"Port diff identified. Attempting to remove the stale port from the Provisioning Template")

        if len(diff) != 1: 
            logger.warning(f"Odd length for a diff between Zoom Positions and Audiocodes INI ({diff}). This should be '1'")
        for i in diff:
            logger.info(f"Diff port index in the INI to remove was was identified as {i}")
            p = fr"TrunkGroup\s{i - 1}\s\=.*"
            f = re.sub(pattern=p, repl="", string=t, flags=re.MULTILINE)
            logger.info("Removed port.")

            if f: return f

            logger.error("Failed to diff the Zoom Positions and the Audiocodes INI. Sync issues likely.")
            sys.stderr.write("Failed to diff the Zoom Positions and the Audiocodes INI. Sync issues likely.")
            sys.stderr.flush()
            return t
    
    logger.info("There were no port differences between the Zoom Positions and the Audiocodes INI.")
    logger.info("This was likely not a port move.")

    return t


for line in sys.stdin:

    """
    stdin will remain open until terminated. rsyslog will terminate this loop after 30 seconds of no messages
    """
    sys.stdout.write("OK")
    sys.stdout.flush()
    
    logger.info("  ")

    msg = line.strip()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # # # 
    hostname = re.search(pattern=r"\s\w+\-.*voice\.umn\.edu", string=msg)

    if not hostname:
        logger.error("Failed to extract the mac hostname from the rsyslog message.")
        sys.stderr.write("Failed to extract the mac hostname from the rsyslog message.")
        sys.stderr.flush()
        continue
    hostname = hostname.group(0).strip()

    logger.info(f"Extracted hostname: {hostname}")
    # # # 

    audiocodes.set_ip(hostname)
    audiocodes.set_base_url(hostname)

    if not audiocodes.test_login():
        logger.error("Failed to log into the audiocodes with the FQND provided by rsyslog")
        sys.stderr.write()
        continue
    
    product_details = audiocodes.get_product_details()

    mac_address = product_details['macAddress'].upper()

    logger.info(f"Extracted mac address: {mac_address}")

    device_details = get_device_from_zoom(m=mac_address, z=zoom_client)

    if not device_details: continue

    trunk_groups = audiocodes.extract_ini_trunk_groups(ini=audiocodes.fetch_ini())
        
    trunk_groups = detect_and_correct_port_move(t=trunk_groups, z=zoom_client, d=device_details["id"])

    # Check to see if this provisioning template exists
    template_id = find_existing_provision_template(m=mac_address, z=zoom_client)

    body = {
        "name" : f"MP1288 Provision Template - {device_details['name']}",
        "description" : f"This template was generated programmatically on {timestamp}. The MAC in the description is needed for the script: {mac_address}.",
        "content" : trunk_groups
    }

    # Template doesn't exist
    if not template_id:
        
        logger.info("Attempting to create a provision template in Zoom.")

        res = zoom_client.add_provision_template(body=body)
        try:
            logger.info("Successfully created a provision template.")
            template_id = res["id"]
        except Exception as e:
            logger.critical(f"Failed to get the provosion template ID: {e}")
            continue


    # Template exists, update it
    else:

        logger.info("Attempting to update a provision template in Zoom.")

        res = zoom_client.update_provision_template(template_id=template_id, body=body)
        try:
            logger.info("Successfully updated a provision template.")
        except Exception as e:
            logger.critical("Failed to get the provision template ID.")
            continue
    
    # Is the tempate bound to the device?
    if device_details["template_id"] != template_id:

        logger.info(f"The Audiocodes doesn't appear to have a provision template assigned to it. Assigning it {template_id}.")

        body = {"provision_template_id" : template_id}

        res = zoom_client.update_device(device_id=device_details["id"], body=body)
        if res.status_code != 204:
            logger.critical(f"Zoom replied with {res.status_code} when attempting to assign this provision template to the device.")
            print("Critical error, see logs.")
            sys.exit(1)
        
        logger.info("Successfully bound this template ID to the device.")
    
    else:
        logger.info("This device has the correct provision template bound to it.")

    

logger.info("Completed with no errors.")