"""
Configuration utilities for loading and parsing YAML configurations.
"""

import sys
from typing import Dict

from rich.console import Console

from .yamler import ConfigNotFoundError, get_region_compartment_pairs

console = Console()


def load_region_compartments(
    project_name: str, stage: str, config_file: str = "meta.yaml"
) -> Dict[str, str]:
    """
    Load region:compartment_id pairs from the YAML configuration.

    Args:
        project_name: Project name from the YAML file
        stage: Stage name from the YAML file
        config_file: Path to the YAML configuration file

    Returns:
        Dict[str, str]: Dictionary with region as key and compartment_id as value

    Exits:
        System exit on configuration errors
    """
    try:
        region_compartments = get_region_compartment_pairs(
            yaml_file_path=config_file, project_name=project_name, stage=stage
        )

        if not region_compartments:
            raise ValueError(
                f"No region:compartment_id pairs found for project '{project_name}' stage '{stage}'"
            )

        return region_compartments

    except ConfigNotFoundError as e:
        console.print(f"[red]Configuration Error: {e}[/red]")
        sys.exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error: {e}[/red]")
        console.print(f"[yellow]Make sure the configuration file exists at: {config_file}[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error loading configuration: {e}[/red]")
        sys.exit(1)
