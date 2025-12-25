class DictComparator:
    def remove_same_property(self, base_dict, compared_dict):
        """主函数：移除相同的属性"""
        self._validate_inputs(base_dict, compared_dict)
        self._validate_dict_structure(base_dict, compared_dict)
        return self._extract_differences(base_dict, compared_dict)

    def _validate_inputs(self, dict1, dict2):
        """验证输入参数"""
        if not isinstance(dict1, dict) or not isinstance(dict2, dict):
            raise ValueError("两个参数都必须是字典类型")

    def _validate_dict_structure(self, dict1, dict2, path=""):
        """验证字典结构一致性"""
        key_validator = KeyValidator()
        type_validator = TypeValidator()

        key_validator.validate_keys_match(dict1, dict2, path)

        for key in dict1.keys():
            current_path = f"{path}.{key}" if path else key
            value1, value2 = dict1[key], dict2[key]

            type_validator.validate_types_match(value1, value2, current_path)

            if isinstance(value1, dict):
                self._validate_dict_structure(value1, value2, current_path)

    def _extract_differences(self, base, compared):
        """提取差异的核心逻辑"""
        diff_result = {}

        for key in base.keys():
            base_val, compared_val = base[key], compared[key]

            if self._are_both_dicts(base_val, compared_val):
                self._process_dict_values(key, base_val, compared_val, diff_result)
            else:
                self._process_non_dict_values(key, base_val, compared_val, diff_result)

        return diff_result

    def _process_dict_values(self, key, base_val, compared_val, diff_result):
        """处理字典类型的值"""
        sub_diff = self._extract_differences(base_val, compared_val)
        if sub_diff:
            diff_result[key] = sub_diff

    def _process_non_dict_values(self, key, base_val, compared_val, diff_result):
        """处理非字典类型的值"""
        if base_val != compared_val:
            diff_result[key] = compared_val

    def _are_both_dicts(self, value1, value2):
        return isinstance(value1, dict) and isinstance(value2, dict)


class KeyValidator:
    """专门负责键的验证"""

    def validate_keys_match(self, dict1, dict2, path):
        if set(dict1.keys()) != set(dict2.keys()):
            raise ValueError(f"字典键不匹配在路径 '{path}': {set(dict1.keys())} vs {set(dict2.keys())}")


class TypeValidator:
    """专门负责类型的验证"""

    def validate_types_match(self, value1, value2, path):
        if isinstance(value1, dict) != isinstance(value2, dict):
            raise ValueError(f"类型不匹配在路径 '{path}': {type(value1)} vs {type(value2)}")