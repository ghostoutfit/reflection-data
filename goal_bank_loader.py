# goal_bank_loader.py

import yaml

def load_goal_bank(filepath="goal_bank.yaml"):
    with open(filepath, "r") as f:
        return yaml.safe_load(f)

def get_goal_text_list(config):
    return [g["text"] for g in config["goals"]]

def get_random_warmup(config, kind="emotional"):
    import random
    return random.choice(config["warmup_prompts"].get(kind, []))

def get_gpt_prompt(config, prompt_type):
    return config["gpt_prompts"].get(prompt_type, "")

def get_config_value(config, key, default=None):
    return config["config"].get(key, default)
