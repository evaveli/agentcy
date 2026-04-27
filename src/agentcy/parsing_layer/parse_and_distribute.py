#src/agentcy/parsing_layer/parse_and_distribute.py

import os
import yaml
from jinja2 import Template, Environment, select_autoescape, FileSystemLoader
from agentcy.pydantic_models.service_registration_model import ServiceRegistration


class InputToInfrastructure:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.template_dir = os.path.join(base_dir, "templates")


    def render_service_yaml(self, service: ServiceRegistration) -> str:
        
        env = Environment(
            loader=FileSystemLoader(searchpath=self.template_dir),
            autoescape=select_autoescape(['yaml', 'yml'])
        )
        template = env.get_template("deployment_template.yaml.j2")
        variables = service.model_dump()
        rendered_yaml = template.render(variables)
        try:
            yaml.safe_load(rendered_yaml)
        except yaml.YAMLError as exc:
            print("Error in rendered YAML:", exc)
            raise
        print(rendered_yaml)
        return rendered_yaml

