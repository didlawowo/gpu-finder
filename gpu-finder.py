#!/usr/bin/env python


"""Example of using the Compute Engine API to create and delete instances.
Creates a new compute engine instance and uses it to apply a caption to
an image.
    https://cloud.google.com/compute/docs/tutorials/python-guide
For more information, see the README.md under /compute.
"""

from loguru import logger
import json
import googleapiclient.discovery
from operator import itemgetter


def check_gpu_config(config):
    compute_config = config
    if compute_config["instance_config"]["machine_type"].startswith("a2"):
        number_of_gpus_requested = compute_config["instance_config"]["number_of_gpus"]
        gpus_in_machine_type = compute_config["instance_config"]["machine_type"][
            (compute_config["instance_config"]["machine_type"].find("highgpu") + 8) : (
                len(compute_config["instance_config"]["machine_type"]) - 1
            )
        ]
        if number_of_gpus_requested != int(gpus_in_machine_type):
            raise Exception(
                "Please match the number of GPUs parameter with the correct machine type in the config file"
            )


def get_zone_info(compute, project):
    zone_list = []
    request = compute.zones().list(project=project)
    while request is not None:
        response = request.execute()
        for zone in response["items"]:
            if zone["status"] == "UP":
                zone_regions = {
                    "region": zone["name"][0 : len(zone["name"]) - 2],
                    "zone": zone["name"],
                }
                zone_list.append(zone_regions)
        request = compute.zones().list_next(
            previous_request=request, previous_response=response
        )
    return zone_list


def check_machine_type_and_accelerator(compute, project, machine_type, gpu_type, zones):
    zone_list = zones
    available_zones = []
    for zone in zone_list:
        request = compute.machineTypes().list(project=project, zone=zone["zone"])
        while request is not None:
            response = request.execute()
            for machine in response["items"]:
                if (
                    "accelerators" in machine
                    and machine["name"] == machine_type
                    and machine["accelerators"][0]["guestAcceleratorType"] == gpu_type
                ):
                    zones_with_instances = {
                        "machine_type": machine["name"],
                        "region": zone["region"],
                        "zone": zone["zone"],
                        "guest_cpus": machine["guestCpus"],
                        "description": machine["description"],
                        "accelerators": machine["accelerators"],
                    }
                    available_zones.append(zones_with_instances)
                elif machine["name"] == machine_type:
                    zones_with_instances = {
                        "machine_type": machine["name"],
                        "region": zone["region"],
                        "zone": zone["zone"],
                        "guest_cpus": machine["guestCpus"],
                        "description": machine["description"],
                    }
                    available_zones.append(zones_with_instances)
            request = compute.machineTypes().list_next(
                previous_request=request, previous_response=response
            )
    if not available_zones:
        raise Exception(f"No machine types of {machine_type} are available")
    return available_zones


def get_accelerator_quota(compute, project, config, zone, requested_gpus):
    zone_list = zone
    accelerator_list = []
    for i in zone_list:
        request = compute.acceleratorTypes().list(project=project, zone=i["zone"])
        while request is not None:
            response = request.execute()
            if "items" in response:
                for accelerator in response["items"]:
                    if accelerator["name"] == config["instance_config"]["gpu_type"]:
                        if requested_gpus <= accelerator["maximumCardsPerInstance"]:
                            accelerator_dict = {
                                "region": i["region"],
                                "zone": i["zone"],
                                "machine_type": i["machine_type"],
                                "guest_cpus": i["guest_cpus"],
                                "name": accelerator["name"],
                                "description": accelerator["description"],
                                "maximum number of GPUs per instance": accelerator[
                                    "maximumCardsPerInstance"
                                ],
                            }
                            accelerator_list.append(accelerator_dict)
                            logger.info(
                                f"{requested_gpus} GPUs requested per instance, {i['zone']} has {accelerator['name']} GPUs with a maximum of {accelerator['maximumCardsPerInstance']} per instance"
                            )
                        else:
                            logger.info(
                                f"{requested_gpus} GPUs requested per instance, {i['zone']} doesn't have enough GPUs, with a maximum of {accelerator['maximumCardsPerInstance']} per instance"
                            )
            request = compute.acceleratorTypes().list_next(
                previous_request=request, previous_response=response
            )
    if not accelerator_list:
        raise Exception(
            f"No accelerator types of {config['instance_config']['gpu_type']} are available with {config['instance_config']['machine_type']} in any zone, or wrong number of GPUs requested"
        )
    return accelerator_list


def get_pricing_info(compute, project, machine_type, gpu_type, zone):
    """Get pricing information for compute and GPU resources."""
    billing = googleapiclient.discovery.build("cloudbilling", "v1")

    # Get the pricing catalog
    pricing_catalog = (
        billing.services()
        .skus()
        .list(
            parent=f"services/6F81-5844-456A",  # Compute Engine service ID
        )
        .execute()
    )

    machine_price = 0
    gpu_price = 0

    # Find machine type pricing
    for sku in pricing_catalog.get("skus", []):
        if (
            sku["category"]["resourceFamily"] == "Compute"
            and sku["description"].startswith(f"Compute {machine_type}")
            and zone in sku.get("serviceRegions", [])
        ):
            for tier in sku["pricingInfo"][0]["pricingExpression"]["tiered_rates"]:
                machine_price = float(tier["unitPrice"]["nanos"]) / 1e9
                break

    # Find GPU pricing
    for sku in pricing_catalog.get("skus", []):
        if (
            sku["category"]["resourceFamily"] == "Compute"
            and gpu_type in sku["description"]
            and "GPU" in sku["description"]
            and zone in sku.get("serviceRegions", [])
        ):
            for tier in sku["pricingInfo"][0]["pricingExpression"]["tiered_rates"]:
                logger.debug(tier)

                gpu_price = float(tier["unitPrice"]["nanos"]) / 1e9
                break
    logger.info(
        f"Service Region: {zone}, Machine Type: {machine_type}, GPU Type: {gpu_type}, Machine Price: {machine_price}, GPU Price: {gpu_price} per hour"
    )
    return machine_price, gpu_price


def process_pricing(accelerators):
    """Process and sort pricing information."""
    pricing_info = []

    for acc in accelerators:
        total_hourly_cost = acc["machine_price"] + (
            acc["gpu_price"] * acc["maximum number of GPUs per instance"]
        )
        pricing_info.append(
            {
                "region": acc["region"],
                "zone": acc["zone"],
                "machine_type": acc["machine_type"],
                "gpu_type": acc["name"],
                "hourly_cost": total_hourly_cost,
                "machine_cost": acc["machine_price"],
                "gpu_cost": acc["gpu_price"],
                "max_gpus": acc["maximum number of GPUs per instance"],
            }
        )

    # Sort by hourly cost
    return sorted(pricing_info, key=itemgetter("hourly_cost"))


def main(gpu_config):
    compute = googleapiclient.discovery.build("compute", "v1")

    if gpu_config["instance_config"]["zone"]:
        logger.info(
            f"Processing selected zones from {gpu_config['instance_config']['zone']}"
        )
        zone_info = get_zone_info(compute, gpu_config["project_id"])
        compute_zones = [
            z for z in zone_info if z["zone"] in gpu_config["instance_config"]["zone"]
        ]
    else:
        logger.info("Processing all zones")
        compute_zones = get_zone_info(compute, gpu_config["project_id"])

    check_gpu_config(gpu_config)
    available_zones = check_machine_type_and_accelerator(
        compute,
        gpu_config["project_id"],
        gpu_config["instance_config"]["machine_type"],
        gpu_config["instance_config"]["gpu_type"],
        compute_zones,
    )

    accelerators = get_accelerator_quota(
        compute,
        gpu_config["project_id"],
        gpu_config,
        available_zones,
        gpu_config["instance_config"]["number_of_gpus"],
    )

    # Add pricing information to accelerators
    for acc in accelerators:
        machine_price, gpu_price = get_pricing_info(
            compute,
            gpu_config["project_id"],
            acc["machine_type"],
            acc["name"],
            acc["zone"],
        )
        acc["machine_price"] = machine_price
        acc["gpu_price"] = gpu_price

    # Process and sort pricing
    pricing_info = process_pricing(accelerators)

    # Display top 3 lowest-priced regions
    logger.info("\nTop 3 lowest-priced regions:")
    for i, info in enumerate(pricing_info[:3], 1):
        logger.info(f"\n{i}. Region: {info['region']} (Zone: {info['zone']})")
        logger.info(f"   Machine Type: {info['machine_type']}")
        logger.info(f"   GPU Type: {info['gpu_type']}")
        logger.info(f"   Total Hourly Cost: ${info['hourly_cost']:.4f}")
        logger.info(f"   - Machine Cost: ${info['machine_cost']:.4f}/hr")
        logger.info(
            f"   - GPU Cost: ${info['gpu_cost']:.4f}/hr per GPU (max {info['max_gpus']} GPUs)"
        )
        logger.info(f"   Monthly Estimate: ${info['hourly_cost'] * 24 * 30:.2f}")


# if __name__ == '__main__':
#     with open('gpu-config.json', 'r', encoding='utf-8') as f:
#         gpu_config = json.load(f)
#     main(gpu_config)

# def main(gpu_config):
#     compute = googleapiclient.discovery.build('compute', 'v1')
#     if gpu_config["instance_config"]["zone"]:
#         logger.info(f"Processing selected zones from {gpu_config['instance_config']['zone']}")
#         zone_info = get_zone_info(compute, gpu_config["project_id"])
#         compute_zones = [z for z in zone_info if z['zone'] in gpu_config['instance_config']['zone']]
#     else:
#         logger.info("Processing all zones")
#         compute_zones = get_zone_info(compute, gpu_config["project_id"])
#     check_gpu_config(gpu_config)
#     # distinct_zones = list({v['zone'] for v in compute_zones})
#     available_zones = check_machine_type_and_accelerator(compute, gpu_config["project_id"], gpu_config["instance_config"]["machine_type"], gpu_config["instance_config"]["gpu_type"], compute_zones)
#     accelerators = get_accelerator_quota(compute, gpu_config["project_id"], gpu_config, available_zones, gpu_config["instance_config"]["number_of_gpus"])
#     available_regions = list({v['region'] for v in available_zones})
#     if available_regions:
#         logger.info(f"Machine type {gpu_config['instance_config']['machine_type']} is available in the following regions: {available_regions}")

#     else:
#         logger.info(f"No regions available with the instance configuration {gpu_config['instance_config']['machine_type']} machine type and {gpu_config['instance_config']['gpu_type']} GPU type")

if __name__ == "__main__":
    with open("gpu-config.json", "r", encoding="utf-8") as f:
        gpu_config = json.load(f)
    main(gpu_config)
