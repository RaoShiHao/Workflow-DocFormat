def remove_extract_description(format_dict, description_dict, exclude_keys):
    save_description = {}
    for key in format_dict:
        base_params = format_dict.get(key)
        compared_params = description_dict.get(key)
        if key in exclude_keys:
            save_description[key] = compared_params
        else:
            save_description[key] = {}
            for attribution, value in compared_params.items():
                if attribution in base_params:
                    save_description[key][attribution] = value
    return save_description

# 基础字典
base_dict = {
    "base_font": {
        "Name": "Arail",
        "NameAscii": "Arail",
        "Size": 20.0,
        "Bold": -1
    },
    "outlinelevel": {
        "outlinelevel": 1
    },
    "alignment": {
        "alignment": "center"
    },
    "spacing": {
        "line_spacing": {
            "value": 18.0,
            "rule": "1.5x"
        },
        "before": 24.0,
        "after": 12.0
    }
}

# 被比较的字典
compared_dict = {
    'base_font': {
        'Name': {'value': 'Arail', 'description': '全局字体名称...'},
        'NameAscii': {'value': 'Arail', 'description': '西文字体名称...'},
        'Size': {'value': 20.0, 'description': '字体大小...'},
        'Bold': {'value': -1, 'description': '是否加粗...'},
        'Italic': {'value': 0, 'description': '是否倾斜...'},  # 这个在base_dict中没有，会被丢弃
        'Underline': {'value': 0, 'description': '下划线样式...'},  # 这个在base_dict中没有，会被丢弃
        'Color': {'value': '#000000', 'description': '字体颜色...'}  # 这个在base_dict中没有，会被丢弃
    },
    'alignment': {
        'value': 'center',
        'description': '段落对齐方式...'
    },
    'outlinelevel': {
        'value': 1,
        'description': '大纲级别...'
    },
    'spacing': {
        'line_spacing': {
            'spacing_rule': {'value': '1.5x', 'description': '行间距规则...'},
            'spacing_value': {'value': 18.0, 'description': '行间距数值...'}
        },
        'before_spacing': {'value': {'pt': 24.0}, 'description': '段前间距...'},
        'after_spacing': {'value': {'pt': 12.0}, 'description': '段后间距...'}
    }
}

# 使用豁免参数
exclude_keys = ['alignment', 'outlinelevel']  # 跳过description字段

result = remove_extract_description(base_dict, compared_dict, exclude_keys)
print(result)