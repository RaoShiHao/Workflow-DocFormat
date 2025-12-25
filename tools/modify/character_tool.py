import os, re
from win32com.client import constants
from tools.modify.tool_config import ContextToolsConfig

class CharacterTools():
    def __init__(self, pyconfig=ContextToolsConfig("/config/Tools/CharacterToolsConfig.yaml")):
        self.config = pyconfig.config
        self.name = self.config.get("name")

    def color_to_int(self, hex_color):
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return b * 65536 + g * 256 + r

    def __set_partial_base_font_by_index(self, doc, paragraph_list, start, length, setting={}):
        """
        设置指定段落中部分字符范围的字体属性

        参数:
            doc: Word文档对象
            paragraph_index: 正文段落索引（从1开始）
            start: 起始字符位置（从1开始）
            length: 修改的字符数
            setting: 包含字体属性的字典

        返回:
            操作结果字典
        """
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]
            result = {}
            for paragraph_index in paragraph_list:
                # 获取段落范围
                if paragraph_index > doc.Paragraphs.Count:
                    raise IndexError(
                        f"Paragraph index {paragraph_index} exceeds document length ({doc.Paragraphs.Count})")
                paragraph_range = doc.Paragraphs(paragraph_index).Range
                if paragraph_range is None:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"Paragraph {paragraph_index} not found or out of range."
                    }
                para_len = paragraph_range.Characters.Count
                if start < 1 or start > para_len or start + length - 1 > para_len:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"Invalid character range: paragraph has {para_len} characters, but requested {start}-{start + length - 1}."
                    }
                # 定位字符范围
                char_range = paragraph_range.Characters(start).Duplicate
                char_range.End = paragraph_range.Characters(start + length - 1).End
                font = char_range.Font
                # 基础字体设置
                for attr in ["Name", "Size", "NameAscii", "Bold", "Italic", "Underline"]:
                    if attr in setting:
                        setattr(font, attr, setting[attr])
                # 颜色设置
                if "Color" in setting and setting["Color"]:
                    hex_color = setting["Color"].lstrip("#")
                    font.Color = self.color_to_int(hex_color)

                # 高亮设置
                if "HighlightColor" in setting:
                    valid_colors = {0, 1, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16}
                    highlight = setting["HighlightColor"]
                    if highlight in valid_colors:
                        char_range.HighlightColorIndex = highlight
                doc.Save()
                result["partial_base_font"] = {
                    "status": "success",
                    "message": f"Partial font properties set successfully for paragraph {paragraph_index}"
                }

            return result
        except Exception as e:
            return {
                "partial_base_font": {
                    "status": "error",
                    "message": f"Failed to set partial font properties: {str(e)}"
                }
            }

    def __set_partial_base_font_by_text(self, doc, paragraph_list, target_text, match_index=1, setting={}):
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]

            result = {}
            for paragraph_index in paragraph_list:
                # 检查段落索引是否有效
                if paragraph_index > doc.Paragraphs.Count:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"Paragraph index {paragraph_index} exceeds document length ({doc.Paragraphs.Count})"
                    }
                    continue
                paragraph = doc.Paragraphs(paragraph_index)
                full_range = paragraph.Range

                match_counter = 0
                matches_applied = 0

                current_start = full_range.Start
                para_end = full_range.End

                while current_start < para_end:
                    search_range = full_range.Duplicate
                    search_range.Start = current_start
                    find = search_range.Find
                    find.Text = target_text
                    find.Forward = True
                    find.MatchCase = False

                    if not find.Execute():
                        break

                    match_counter += 1

                    if match_index == "all" or match_index == match_counter:
                        rng = search_range.Duplicate
                        font = rng.Font

                        # 设置字体属性
                        for attr in ["Name", "Size", "NameAscii", "Bold", "Italic", "Underline"]:
                            if attr in setting:
                                setattr(font, attr, setting[attr])

                        # 设置颜色
                        if "Color" in setting and setting["Color"]:
                            hex_color = setting["Color"].lstrip("#")
                            font.Color = self.color_to_int(hex_color)


                        # 设置高亮
                        if "HighlightColor" in setting:
                            highlight = setting["HighlightColor"]
                            valid_colors = {0, 1, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16}
                            if highlight in valid_colors:
                                rng.HighlightColorIndex = highlight

                        matches_applied += 1

                        if isinstance(match_index, int) and match_index == match_counter:
                            break

                    # 继续查找下一个
                    current_start = search_range.End

                if matches_applied == 0:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"No match found for paragraph '{target_text}' with index {match_index} in paragraph {paragraph_index}"
                    }
                else:
                    result["partial_base_font"] = {
                        "status": "success",
                        "message": f"Applied font settings to {matches_applied} match(es) of paragraph '{target_text}' in paragraph {paragraph_index}"
                    }

            doc.Save()
            return result

        except Exception as e:
            return {
                "partial_base_font": {
                    "status": "error",
                    "message": f"Failed to set partial font by paragraph: {str(e)}"
                }
            }

    def __set_partial_base_font_by_regex(self, doc, paragraph_list, regex_pattern, match_index=1, setting={}):
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]
            result = {}
            for paragraph_index in paragraph_list:
                # 检查段落索引是否有效
                if paragraph_index > doc.Paragraphs.Count:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"Paragraph index {paragraph_index} exceeds document length ({doc.Paragraphs.Count})"
                    }
                    continue
                paragraph = doc.Paragraphs(paragraph_index)
                text = paragraph.Range.Text
                matches = list(re.finditer(regex_pattern, text))
                if not matches:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"No match found for regex pattern '{regex_pattern}' in paragraph {paragraph_index}"
                    }
                    continue
                match_indices = []
                if match_index == "all":
                    match_indices = list(range(len(matches)))
                elif isinstance(match_index, int) and 1 <= match_index <= len(matches):
                    match_indices = [match_index - 1]
                else:
                    result["partial_base_font"] = {
                        "status": "error",
                        "message": f"No match at index {match_index} for regex pattern '{regex_pattern}' in paragraph {paragraph_index}"
                    }
                    continue
                base_range = paragraph.Range
                for i in match_indices:
                    match = matches[i]
                    start_offset = match.start()
                    end_offset = match.end()
                    # 创建具体字符范围
                    rng = base_range.Duplicate
                    rng.Start = base_range.Start + start_offset
                    rng.End = base_range.Start + end_offset
                    font = rng.Font
                    # 设置字体属性
                    for attr in ["Name", "Size", "NameAscii", "Bold", "Italic", "Underline"]:
                        if attr in setting:
                            setattr(font, attr, setting[attr])
                    # 设置颜色
                    if "Color" in setting and setting["Color"]:
                        hex_color = setting["Color"].lstrip("#")
                        font.Color = self.color_to_int(hex_color)

                    # 设置高亮
                    if "HighlightColor" in setting:
                        highlight = setting["HighlightColor"]
                        valid_colors = {0, 1, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16}
                        if highlight in valid_colors:
                            rng.HighlightColorIndex = highlight
                result["partial_base_font"] = {
                    "status": "success",
                    "message": f"Applied font settings to {len(match_indices)} match(es) for regex pattern '{regex_pattern}' in paragraph {paragraph_index}"
                }
            doc.Save()
            return result
        except Exception as e:
            return {
                "partial_base_font": {
                    "status": "error",
                    "message": f"Failed to set partial font by regex: {str(e)}"
                }
            }

    def __set_partial_advance_font_by_index(self, doc, paragraph_list, start, length, setting={}):
        """
        设置指定段落中部分字符范围的高级字体属性

        参数:
            doc: Word文档对象
            paragraph_list: 正文段落索引列表（从1开始）或包含'all'的列表
            start: 起始字符位置（从1开始）
            length: 修改的字符数
            setting: 包含字体效果的字典，支持以下字段:
                - "StrikeThrough": 删除线（bool）
                - "Subscript": 下标（bool）
                - "Superscript": 上标（bool）
                - "AllCaps": 全大写（bool）
                - "SmallCaps": 小型大写字母（bool）
                - "Spacing": 字符间距（float）
                - "Scaling": 缩放百分比（int, 1-600）
                - "Emboss": 浮雕效果（bool）
                - "Engrave": 雕刻效果（bool）
                - "Shadow": 阴影效果（bool）

        返回:
            操作结果字典
        """
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]

            # 验证Scaling参数
            if "Scaling" in setting:
                value = setting["Scaling"]
                if not (1 <= value <= 600):
                    raise ValueError("Scaling must be between 1 and 600")

            result = {}
            for paragraph_index in paragraph_list:
                # 检查段落索引是否有效
                if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"Paragraph index {paragraph_index} is out of bounds. Document only has {doc.Paragraphs.Count} paragraphs."
                    }
                    continue

                paragraph_range = doc.Paragraphs(paragraph_index).Range
                para_len = paragraph_range.Characters.Count

                if start < 1 or start > para_len or start + length - 1 > para_len:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"Invalid character range: paragraph {paragraph_index} has {para_len} characters, but requested {start} to {start + length - 1}."
                    }
                    continue

                # 获取字符范围
                char_range = paragraph_range.Characters(start).Duplicate
                char_range.End = paragraph_range.Characters(start + length - 1).End
                font = char_range.Font

                # 设置字体效果
                for attr, value in setting.items():
                    setattr(font, attr, value)

                result["partial_advance_font"] = {
                    "status": "success",
                    "message": f"Advanced font effects applied to paragraph {paragraph_index}, characters {start}-{start + length - 1}"
                }

            doc.Save()
            return result

        except Exception as e:
            return {
                "partial_advance_font": {
                    "status": "error",
                    "message": f"Failed to set advanced font effects: {str(e)}"
                }
            }

    def __set_partial_advance_font_by_text(self, doc, paragraph_list, target_text, match_index=1, setting={}):
        """
        设置指定段落中匹配文本的高级字体效果

        参数:
            doc: Word文档对象
            paragraph_list: 段落索引列表（从1开始）或包含'all'的列表
            target_text: 要匹配的文本内容
            match_index: 第几个匹配项（int 或 "all"）
            setting: 包含字体设置的字典（支持高级效果）

        返回:
            操作结果字典
        """
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]

            # 支持的高级字体字段验证
            if "Scaling" in setting:
                value = setting["Scaling"]
                if not (1 <= value <= 600):
                    raise ValueError("Scaling must be between 1 and 600")

            result = {}
            for paragraph_index in paragraph_list:
                # 检查段落索引是否有效
                if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"Paragraph index {paragraph_index} is out of bounds. Document has {doc.Paragraphs.Count} paragraphs."
                    }
                    continue

                paragraph = doc.Paragraphs(paragraph_index)
                full_range = paragraph.Range
                para_end = full_range.End

                current_start = full_range.Start
                match_counter = 0
                matches_applied = 0

                while current_start < para_end:
                    search_range = full_range.Duplicate
                    search_range.Start = current_start

                    find = search_range.Find
                    find.Text = target_text
                    find.Forward = True
                    find.MatchCase = False

                    if not find.Execute():
                        break

                    match_counter += 1

                    if match_index == "all" or match_index == match_counter:
                        rng = search_range.Duplicate
                        font = rng.Font

                        # 设置高级字体效果
                        for attr, value in setting.items():
                            setattr(font, attr, value)

                        matches_applied += 1

                        if isinstance(match_index, int) and match_index == match_counter:
                            break

                    current_start = search_range.End

                if matches_applied == 0:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"No match found for '{target_text}' at index {match_index} in paragraph {paragraph_index}"
                    }
                else:
                    result["partial_advance_font"] = {
                        "status": "success",
                        "message": f"Applied advanced font effects to {matches_applied} match(es) of '{target_text}' in paragraph {paragraph_index}"
                    }

            doc.Save()
            return result

        except Exception as e:
            return {
                "partial_advance_font": {
                    "status": "error",
                    "message": f"Failed to set advanced font effects: {str(e)}"
                }
            }

    def __set_partial_advance_font_by_regex(self, doc, paragraph_list, regex_pattern, match_index=1, setting={}):
        try:
            if 'all' in paragraph_list:
                paragraph_list = [i + 1 for i in range(doc.Paragraphs.Count)]

            # 验证Scaling参数
            if "Scaling" in setting:
                value = setting["Scaling"]
                if not (1 <= value <= 600):
                    raise ValueError("Scaling must be between 1 and 600")

            result = {}
            for paragraph_index in paragraph_list:
                # 检查段落索引是否有效
                if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"Paragraph index {paragraph_index} is out of bounds. Document has {doc.Paragraphs.Count} paragraphs."
                    }
                    continue

                paragraph = doc.Paragraphs(paragraph_index)
                full_text = paragraph.Range.Text
                matches = list(re.finditer(regex_pattern, full_text))

                if not matches:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"No match found for pattern '{regex_pattern}' in paragraph {paragraph_index}"
                    }
                    continue

                match_indices = []
                if match_index == "all":
                    match_indices = list(range(len(matches)))
                elif isinstance(match_index, int) and 1 <= match_index <= len(matches):
                    match_indices = [match_index - 1]
                else:
                    result["partial_advance_font"] = {
                        "status": "error",
                        "message": f"No match at index {match_index} for pattern '{regex_pattern}' in paragraph {paragraph_index}"
                    }
                    continue

                for idx in match_indices:
                    match = matches[idx]
                    start = paragraph.Range.Start + match.start()
                    end = paragraph.Range.Start + match.end()

                    rng = doc.Range(Start=start, End=end)
                    font = rng.Font

                    # 设置高级字体效果
                    for attr, value in setting.items():
                        setattr(font, attr, value)

                result["partial_advance_font"] = {
                    "status": "success",
                    "message": f"Applied advanced font effects to {len(match_indices)} match(es) for pattern '{regex_pattern}' in paragraph {paragraph_index}"
                }

            doc.Save()
            return result

        except Exception as e:
            return {
                "partial_advance_font": {
                    "status": "error",
                    "message": f"Failed to set advanced font effects by regex: {str(e)}"
                }
            }

    def set_partial_base_font(self, doc, location_list, mode, params=None, setting={}):
        """
        统一设置部分文字的高级字体属性
        Args:
            doc: 文档对象
            location_list: 段落索引
            mode: 匹配模式，可选 'regex', 'paragraph', 'index'
            params: 参数字典，根据模式不同包含不同的参数
                - regex模式: {'pattern': regex_pattern, 'match_index': match_index}
                - text模式: {'paragraph': target_text, 'match_index': match_index}
                - index模式: {'start': start_index, 'length': length}
            setting: 字体设置字典
        """
        if params is None:
            params = {}
        if mode == 'regex':
            return self.__set_partial_base_font_by_regex(
                doc, location_list,
                params.get('pattern'),
                params.get('match_index', 1),
                setting
            )
        elif mode == 'paragraph':
            return self.__set_partial_base_font_by_text(
                doc, location_list,
                params.get('paragraph'),
                params.get('match_index', 1),
                setting
            )
        elif mode == 'index':
            return self.__set_partial_base_font_by_index(
                doc, location_list,
                params.get('start'),
                params.get('length'),
                setting
            )
        else:
            result = {}
            result["mode"] = {
                "status": "error",
                "message": f"不支持的mode参数: {mode}，可选 'regex', 'paragraph', 'index'"
            }
            return result

    def set_partial_advanced_font(self, doc, location_list, mode, params=None, setting={}):
        """
        统一设置部分文字的高级字体属性

        Args:
            doc: 文档对象
            location_list: 段落索引列表
            mode: 匹配模式，可选 'regex', 'paragraph', 'index'
            params: 参数字典，根据模式不同包含不同的参数
                - regex模式: {'pattern': regex_pattern, 'match_index': match_index}
                - text模式: {'paragraph': target_text, 'match_index': match_index}
                - index模式: {'start': start_index, 'length': length}
            setting: 字体设置字典
        """
        if params is None:
            params = {}

        if mode == 'regex':
            return self.__set_partial_advance_font_by_regex(
                doc, location_list,
                params.get('pattern'),
                params.get('match_index', 1),
                setting
            )
        elif mode == 'paragraph':
            return self.__set_partial_advance_font_by_text(
                doc, location_list,
                params.get('paragraph'),
                params.get('match_index', 1),
                setting
            )
        elif mode == 'index':
            return self.__set_partial_advance_font_by_index(
                doc, location_list,
                params.get('start'),
                params.get('length'),
                setting
            )
        else:
            result = {}
            result["mode"] = {
                "status": "error",
                "message": f"不支持的mode参数: {mode}，可选 'regex', 'paragraph', 'index'"
            }
            return result

    def set_partial_font(self, doc, location_list, mode, params=None, base_setting={}, advanced_setting={}):
        self.set_partial_base_font(doc,location_list,mode,params,base_setting)
        self.set_partial_advanced_font(doc,location_list,mode,params,advanced_setting)