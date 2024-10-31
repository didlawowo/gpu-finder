#!/usr/bin/env python
import googleapiclient.discovery
from loguru import logger
import json
from operator import itemgetter

def get_pricing_info(compute, project, machine_type, gpu_type, zone):
    """Get pricing information for compute and GPU resources."""
    billing = googleapiclient.discovery.build('cloudbilling', 'v1')
    
    # Get the pricing catalog
    pricing_catalog = billing.services().skus().list(
        parent=f'services/6F81-5844-456A',  # Compute Engine service ID
    ).execute()
    
    machine_price = 0
    gpu_price = 0
    
    # Find machine type pricing
    for sku in pricing_catalog.get('skus', []):
        if (sku['category']['resourceFamily'] == 'Compute' and 
            sku['description'].startswith(f'Compute {machine_type}') and 
            zone in sku.get('serviceRegions', [])):
            for tier in sku['pricingInfo'][0]['pricingExpression']['tiered_rates']:
                machine_price = float(tier['unitPrice']['nanos']) / 1e9
                break
                
    # Find GPU pricing
    for sku in pricing_catalog.get('skus', []):
        if (sku['category']['resourceFamily'] == 'Compute' and 
            gpu_type in sku['description'] and 
            'GPU' in sku['description'] and 
            zone in sku.get('serviceRegions', [])):
            for tier in sku['pricingInfo'][0]['pricingExpression']['tiered_rates']:
                gpu_price = float(tier['unitPrice']['nanos']) / 1e9
                break
    
    return machine_price, gpu_price

def process_pricing(accelerators):
    """Process and sort pricing information."""
    pricing_info = []
    
    for acc in accelerators:
        total_hourly_cost = acc['machine_price'] + (acc['gpu_price'] * acc['maximum number of GPUs per instance'])
        pricing_info.append({
            'region': acc['region'],
            'zone': acc['zone'],
            'machine_type': acc['machine_type'],
            'gpu_type': acc['name'],
            'hourly_cost': total_hourly_cost,
            'machine_cost': acc['machine_price'],
            'gpu_cost': acc['gpu_price'],
            'max_gpus': acc['maximum number of GPUs per instance']
        })
    
    # Sort by hourly cost
    return sorted(pricing_info, key=itemgetter('hourly_cost'))

def main(gpu_config):
    compute = googleapiclient.discovery.build('compute', 'v1')
    
    if gpu_config["instance_config"]["zone"]:
        logger.info(f"Processing selected zones from {gpu_config['instance_config']['zone']}")
        zone_info = get_zone_info(compute, gpu_config["project_id"])
        compute_zones = [z for z in zone_info if z['zone'] in gpu_config['instance_config']['zone']]
    else:
        logger.info("Processing all zones")
        compute_zones = get_zone_info(compute, gpu_config["project_id"])
    
    check_gpu_config(gpu_config)
    available_zones = check_machine_type_and_accelerator(
        compute, 
        gpu_config["project_id"], 
        gpu_config["instance_config"]["machine_type"], 
        gpu_config["instance_config"]["gpu_type"], 
        compute_zones
    )
    
    accelerators = get_accelerator_quota(
        compute, 
        gpu_config["project_id"], 
        gpu_config, 
        available_zones, 
        gpu_config["instance_config"]["number_of_gpus"]
    )
    
    # Add pricing information to accelerators
    for acc in accelerators:
        machine_price, gpu_price = get_pricing_info(
            compute,
            gpu_config["project_id"],
            acc['machine_type'],
            acc['name'],
            acc['zone']
        )
        acc['machine_price'] = machine_price
        acc['gpu_price'] = gpu_price
    
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
        logger.info(f"   - GPU Cost: ${info['gpu_cost']:.4f}/hr per GPU (max {info['max_gpus']} GPUs)")
        logger.info(f"   Monthly Estimate: ${info['hourly_cost'] * 24 * 30:.2f}")

if __name__ == '__main__':
    with open('gpu-config.json', 'r', encoding='utf-8') as f:
        gpu_config = json.load(f)
    main(gpu_config)