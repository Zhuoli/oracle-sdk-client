import yaml
from typing import Optional, Dict, Any


class ConfigNotFoundError(Exception):
    """Custom exception for configuration not found errors."""
    pass


def get_compartment_id(
    yaml_file_path: str,
    project_name: str,
    stage: str,
    realm: str,
    region: str
) -> str:
    """
    Read a YAML configuration file and retrieve the compartment_id based on the provided parameters.
    
    Args:
        yaml_file_path: Path to the YAML configuration file
        project_name: Name of the project (e.g., 'remote-observer', 'today-all')
        stage: Deployment stage (e.g., 'dev', 'staging', 'prod')
        realm: Realm identifier (e.g., 'oc1', 'oc16', 'oc17')
        region: Region identifier (e.g., 'us-phoenix-1', 'us-ashburn-1')
    
    Returns:
        str: The compartment_id for the specified configuration
    
    Raises:
        ConfigNotFoundError: If the specified configuration path is not found
        FileNotFoundError: If the YAML file cannot be found
        yaml.YAMLError: If the YAML file is malformed
    """
    try:
        # Load the YAML file
        with open(yaml_file_path, 'r') as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"YAML file not found at path: {yaml_file_path}")
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Error parsing YAML file: {e}")
    
    # Navigate through the configuration structure
    error_path = []
    
    # Check if 'projects' exists
    if 'projects' not in config:
        raise ConfigNotFoundError("'projects' key not found in configuration")
    error_path.append('projects')
    
    # Check if project_name exists
    if project_name not in config['projects']:
        available_projects = list(config['projects'].keys())
        raise ConfigNotFoundError(
            f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
        )
    error_path.append(project_name)
    
    # Check if stage exists
    if stage not in config['projects'][project_name]:
        available_stages = list(config['projects'][project_name].keys())
        raise ConfigNotFoundError(
            f"Stage '{stage}' not found for project '{project_name}'. "
            f"Available stages: {', '.join(available_stages)}"
        )
    error_path.append(stage)
    
    # Check if realm exists
    if realm not in config['projects'][project_name][stage]:
        available_realms = list(config['projects'][project_name][stage].keys())
        raise ConfigNotFoundError(
            f"Realm '{realm}' not found for path projects.{project_name}.{stage}. "
            f"Available realms: {', '.join(available_realms)}"
        )
    error_path.append(realm)
    
    # Check if region exists
    if region not in config['projects'][project_name][stage][realm]:
        available_regions = list(config['projects'][project_name][stage][realm].keys())
        raise ConfigNotFoundError(
            f"Region '{region}' not found for path projects.{project_name}.{stage}.{realm}. "
            f"Available regions: {', '.join(available_regions)}"
        )
    error_path.append(region)
    
    # Check if compartment_id exists
    region_config = config['projects'][project_name][stage][realm][region]
    if not isinstance(region_config, dict) or 'compartment_id' not in region_config:
        raise ConfigNotFoundError(
            f"'compartment_id' not found for path projects.{project_name}.{stage}.{realm}.{region}"
        )
    
    return region_config['compartment_id']


def get_compartment_id_safe(
    yaml_file_path: str,
    project_name: str,
    stage: str,
    realm: str,
    region: str,
    default: Optional[str] = None
) -> Optional[str]:
    """
    Safe version of get_compartment_id that returns a default value on error.
    
    Args:
        yaml_file_path: Path to the YAML configuration file
        project_name: Name of the project
        stage: Deployment stage
        realm: Realm identifier
        region: Region identifier
        default: Default value to return if configuration is not found
    
    Returns:
        Optional[str]: The compartment_id or the default value if not found
    """
    try:
        return get_compartment_id(yaml_file_path, project_name, stage, realm, region)
    except (ConfigNotFoundError, FileNotFoundError, yaml.YAMLError):
        return default


def get_region_compartment_pairs(
    yaml_file_path: str,
    project_name: str,
    stage: str
) -> Dict[str, str]:
    """
    Get all region:compartment_id pairs for a given project and stage.
    
    Args:
        yaml_file_path: Path to the YAML configuration file
        project_name: Name of the project (e.g., 'remote-observer', 'today-all')
        stage: Deployment stage (e.g., 'dev', 'staging', 'prod')
    
    Returns:
        Dict[str, str]: Dictionary with region as key and compartment_id as value
    
    Raises:
        ConfigNotFoundError: If the specified project or stage is not found
        FileNotFoundError: If the YAML file cannot be found
        yaml.YAMLError: If the YAML file is malformed
    """
    try:
        # Load the YAML file
        with open(yaml_file_path, 'r') as file:
            config = yaml.safe_load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"YAML file not found at path: {yaml_file_path}")
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Error parsing YAML file: {e}")
    
    # Check if 'projects' exists
    if 'projects' not in config:
        raise ConfigNotFoundError("'projects' key not found in configuration")
    
    # Check if project_name exists
    if project_name not in config['projects']:
        available_projects = list(config['projects'].keys())
        raise ConfigNotFoundError(
            f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
        )
    
    # Check if stage exists
    if stage not in config['projects'][project_name]:
        available_stages = list(config['projects'][project_name].keys())
        raise ConfigNotFoundError(
            f"Stage '{stage}' not found for project '{project_name}'. "
            f"Available stages: {', '.join(available_stages)}"
        )
    
    # Extract region:compartment_id pairs from all realms
    region_compartment_pairs = {}
    stage_config = config['projects'][project_name][stage]
    
    # Iterate through all realms (oc1, oc16, oc17, etc.)
    for realm, regions in stage_config.items():
        # Iterate through all regions in this realm
        for region, region_config in regions.items():
            if isinstance(region_config, dict) and 'compartment_id' in region_config:
                # Use region as key, compartment_id as value
                region_compartment_pairs[region] = region_config['compartment_id']
    
    return region_compartment_pairs


def list_available_configs(yaml_file_path: str) -> Dict[str, Any]:
    """
    List all available configurations in the YAML file.
    
    Args:
        yaml_file_path: Path to the YAML configuration file
    
    Returns:
        Dict containing the structure of available configurations
    """
    try:
        with open(yaml_file_path, 'r') as file:
            config = yaml.safe_load(file)
        
        available = {}
        if 'projects' in config:
            for project, stages in config['projects'].items():
                available[project] = {}
                for stage, realms in stages.items():
                    available[project][stage] = {}
                    for realm, regions in realms.items():
                        available[project][stage][realm] = list(regions.keys())
        
        return available
    except Exception as e:
        return {"error": str(e)}


# Example usage
if __name__ == "__main__":
    # Example 1: Get compartment_id with error handling
    try:
        compartment_id = get_compartment_id(
            yaml_file_path="meta.yaml",
            project_name="remote-observer",
            stage="dev",
            realm="oc1",
            region="us-phoenix-1"
        )
        print(f"Compartment ID: {compartment_id}")
    except ConfigNotFoundError as e:
        print(f"Configuration error: {e}")
    except FileNotFoundError as e:
        print(f"File error: {e}")
    except yaml.YAMLError as e:
        print(f"YAML parsing error: {e}")
    
    # Example 2: Get compartment_id with safe version (returns None on error)
    compartment_id = get_compartment_id_safe(
        yaml_file_path="meta.yaml",
        project_name="today-all",
        stage="prod",
        realm="oc1",
        region="us-phoenix-1",
        default="DEFAULT_COMPARTMENT_ID"
    )
    print(f"Safe Compartment ID: {compartment_id}")
    
    # Example 3: List all available configurations
    available = list_available_configs("meta.yaml")
    print("\nAvailable configurations:")
    for project, stages in available.items():
        print(f"  Project: {project}")
        for stage, realms in stages.items():
            print(f"    Stage: {stage}")
            for realm, regions in realms.items():
                print(f"      Realm: {realm} -> Regions: {regions}")