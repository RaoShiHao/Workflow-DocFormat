import yaml,os
from constant import ABS_DIR
class ContextToolsConfig:
    def __init__(self, config_path=""):
        abs_path = os.path.join(ABS_DIR,config_path)
        # print(abs_path)
        with open(abs_path, "r",encoding='utf-8') as f:
            self.config = yaml.safe_load(f)