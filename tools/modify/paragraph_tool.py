import os, re
from win32com.client import constants
import win32com
from tools.modify.tool_config import ContextToolsConfig

class BaseTextTools():
    def __init__(self):
        self.excel = win32com.client.Dispatch("Excel.Application")

    def convert_to_pt(self, value, unit):
        execl = self.excel
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit == "pt" or unit == "point":
            return float(value)
        elif unit == "cm":
            return execl.CentimetersToPoints(value)
        elif unit == "mm":
            return execl.CentimetersToPoints(value*0.1)
        elif unit == "inches":
            return execl.InchesToPoints(value)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def is_caption_paragraph(self,paragraph):
        """判断是否为表格/图片标题段落（基于正则表达式）"""
        text = paragraph.Range.Text.strip()
        # 匹配中英文常见标题格式（表1：/Table 1: 等）
        pattern = r'^((表|图|表格|Table|Figure)[\s]*\d+[\s]*[:：])'
        return bool(re.match(pattern, text))

    def format_natural_paragraph(self, doc, paragraph_index):
        """
        对自然语义的第N段应用格式调整
        （自动跳过表格、图片、空白段以及表名/图名）

        参数：
            doc: Word文档对象
            paragraph_index: 自然段落的序号（从1开始）

        返回：
            Word Range对象，如果找不到指定段落则返回None
        """
        try:
            # 筛选真正的文本段落（四重过滤）
            text_paragraphs = [
                para for para in doc.Paragraphs
                if (para.Range.Tables.Count == 0 and  # 不是表格
                    para.Range.InlineShapes.Count == 0 and  # 不是图片
                    para.Range.Text.strip() and  # 不是空白段
                    not self.is_caption_paragraph(para))  # 不是表名/图名
            ]
            # 安全检查
            if not text_paragraphs:
                return None
            if paragraph_index < 1 or paragraph_index > len(text_paragraphs):
                return None
            # 返回目标段落的Range对象
            return text_paragraphs[paragraph_index - 1].Range
        except Exception:
            return None

    def set_base_font(self, doc, location_list, setting={}):
        """
        根据位置列表设置 Word 文档中指定段落或选区的字体属性

        参数:
            doc: Word 文档对象
            location_list: 位置列表，0表示当前选区，正整数表示段落索引
            setting: 包含字体属性的字典

        返回:
            包含操作结果的字典
        """
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        try:
            for location in location_list:
                # 处理当前选区 (location = 0)
                if location == 0:
                    range_obj = doc.Application.Selection.Range
                elif location > 0:
                    if location > doc.Paragraphs.Count:
                        raise IndexError(f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})")
                    range_obj = doc.Paragraphs(location).Range
                else:
                    raise ValueError("Location must be non-negative integer")

                font = range_obj.Font
                # 设置字体属性
                for attr in ["Name", "Size", "NameAscii", "Bold", "Italic", "Underline"]:
                    if attr in setting:
                        setattr(font, attr, setting[attr])

                if "Color" in setting and setting["Color"]:
                    hex_color = setting["Color"].lstrip("#")
                    r = int(hex_color[0:2], 16)
                    g = int(hex_color[2:4], 16)
                    b = int(hex_color[4:6], 16)
                    # 交换R和B分量
                    font.Color = b * 65536 + g * 256 + r

                # 设置高亮
                if "HighlightColor" in setting:
                    highlight = setting["HighlightColor"]
                    valid_colors = {0, 1, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16}
                    if highlight in valid_colors:
                        range_obj.HighlightColorIndex = highlight
                    else:
                        raise ValueError(f"HighlightColor not support: {setting['HighlightColor']}")

            doc.Save()
            results["base_font"] = {
                "status": "success",
                "message": "Font properties set successfully"
            }
        except Exception as e:
            results["base_font"] = {
                "status": "error",
                "message": f"Failed to set font properties: {str(e)}"
            }
        return results

    def set_advanced_font(self, doc, location_list, setting={}):
        """
        Set advanced font effects for multiple paragraphs or selection in Word document

        Args:
            doc: Word document object
            location_list: List of positions (0 for current selection, positive integers for natural paragraph indices)
            setting: Dictionary containing font effects
        Returns:
            Dictionary with operation results
        """
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        # 支持的字段及其描述
        supported_fields = {
            "StrikeThrough": "Strike-through",
            "Subscript": "Subscript",
            "Superscript": "Superscript",
            "AllCaps": "All caps",
            "SmallCaps": "Small caps",
            "Spacing": "Character spacing",
            "Scaling": "Character scaling",
            "Emboss": "Emboss",
            "Engrave": "Engrave",
            "Shadow": "Shadow"
        }

        try:
            for paragraph_index in location_list:
                # 获取目标 range
                if paragraph_index == 0:
                    range_obj = doc.Application.Selection.Range
                elif paragraph_index > 0:
                    range_obj = doc.Paragraphs(paragraph_index).Range
                    if range_obj is None:
                        raise ValueError(f"Position {paragraph_index} not found or invalid natural paragraph")
                else:
                    raise ValueError("Paragraph index must be non-negative integer")

                font = range_obj.Font
                for attr, desc in supported_fields.items():
                    if attr not in setting:
                        continue
                    value = setting[attr]
                    if attr == "Scaling":
                        if not (1 <= value <= 600):
                            raise ValueError("Scaling must be between 1 and 600")
                    setattr(font, attr, value)
            results["advanced_font"] = {
                "status": "success",
                "message": "Advanced font effects set successfully"
            }
            doc.Save()
        except Exception as e:
            results["advanced_font"] = {
                "status": "error",
                "message": f"Failed to set advanced font effects: {str(e)}"
            }
        return results

    def set_outlinelevel(self, doc, location_list, outlinelevel):
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        # Validate outline level first (before processing any paragraphs)
        try:
            outlinelevel = int(outlinelevel)
            if outlinelevel < 1 or outlinelevel > 10:
                raise ValueError("Outline level must be between 1-9 (heading levels) or 10 (body paragraph)")
            level_description = "Body paragraph" if outlinelevel == 10 else f"Heading level {outlinelevel}"
        except Exception as e:
            # If outline level is invalid, return error
            results["outlinelevel"] = {
                'status': 'error',
                'message': f"Invalid outline level: {str(e)}"
            }
            doc.Save()
            return results

        try:
            for location in location_list:
                if location == 0:
                    range_obj = doc.Application.Selection.Range
                elif location > 0:
                    if location > doc.Paragraphs.Count:
                        raise IndexError(f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})")
                    range_obj = doc.Paragraphs(location).Range
                else:
                    raise ValueError("Location must be non-negative integer")

                # Set outline level for the range
                para_fmt = range_obj.ParagraphFormat
                para_fmt.OutlineLevel = outlinelevel

            results["outlinelevel"] = {
                "status": "success",
                "message": f"Set to {level_description}"
            }
            doc.Save()
        except Exception as e:
            results["outlinelevel"] = {
                "status": "error",
                "message": f"Failed to set outline level: {str(e)}"
            }

        return results

    def set_alignment(self, doc, location_list, alignment):
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        # Define alignment mapping
        alignment_map = {
            "left": constants.wdAlignParagraphLeft,
            "center": constants.wdAlignParagraphCenter,
            "right": constants.wdAlignParagraphRight,
            "justify": constants.wdAlignParagraphJustify,
            "distribute": constants.wdAlignParagraphDistribute,
            "左对齐": constants.wdAlignParagraphLeft,
            "居中": constants.wdAlignParagraphCenter,
            "右对齐": constants.wdAlignParagraphRight,
            "两端对齐": constants.wdAlignParagraphJustify,
            "分散对齐": constants.wdAlignParagraphDistribute,
        }

        # Validate alignment first
        if alignment not in alignment_map:
            results["alignment"] = {
                "status": "error",
                "message": f"Invalid alignment type: {alignment}"
            }
            return results

        alignment_desc = {
            constants.wdAlignParagraphLeft: "Left aligned",
            constants.wdAlignParagraphCenter: "Center aligned",
            constants.wdAlignParagraphRight: "Right aligned",
            constants.wdAlignParagraphJustify: "Justified",
            constants.wdAlignParagraphDistribute: "Distributed"
        }[alignment_map[alignment]]

        try:
            for location in location_list:
                if location == 0:
                    range_obj = doc.Application.Selection.Range
                elif location > 0:
                    if location > doc.Paragraphs.Count:
                        raise IndexError(
                            f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})"
                        )
                    range_obj = doc.Paragraphs(location).Range
                else:
                    raise ValueError("Location must be non-negative integer")

                # Set alignment for the range
                para_fmt = range_obj.ParagraphFormat
                para_fmt.Alignment = alignment_map[alignment]

            results["alignment"] = {
                "status": "success",
                "message": alignment_desc
            }

        except Exception as e:
            results["alignment"] = {
                "status": "error",
                "message": f"Failed to set alignment: {str(e)}"
            }

        # Save document (non-critical operation)
        try:
            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to set alignment: {str(e)}"
            }

        return results

    def set_pagination_control(self, doc, location_list, widow_control=None, keep_with_next=None,
                               keep_together=None, page_break_before=None):
        """
        Set pagination control properties for multiple locations in a Word document

        Args:
            doc: Word document object
            location_list: List of positions (0 for current selection, positive integers for paragraph indices)
            widow_control: Control widow/orphan lines (prevents first/last line of paragraph appearing alone)
            keep_with_next: Keep paragraph with next paragraph
            keep_together: Keep lines in paragraph together (no page break within)
            page_break_before: page break before paragraph

        Returns:
            dict: results in the format:
            {
                "widow_control": {"status": str, "message": str},
                "keep_with_next": {"status": str, "message": str},
                "keep_together": {"status": str, "message": str},
                "page_break_before": {"status": str, "message": str}
            }
        """
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        try:
            for location in location_list:
                # Handle current selection (location = 0)
                if location == 0:
                    range_obj = doc.Application.Selection.Range
                # Handle specified paragraph (location > 0)
                elif location > 0:
                    if location > doc.Paragraphs.Count:
                        raise IndexError(f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})")
                    range_obj = doc.Paragraphs(location).Range
                else:
                    raise ValueError("Location must be non-negative integer")

                # Set pagination properties
                para_fmt = range_obj.ParagraphFormat

                if widow_control is not None:
                    try:
                        para_fmt.WidowControl = widow_control
                        results["widow_control"] = {
                            "status": "success",
                            "message": f"Set to {'on' if widow_control else 'off'}"
                        }
                    except Exception as e:
                        results["widow_control"] = {
                            "status": "error",
                            "message": f"Failed to set WidowControl: {str(e)}"
                        }

                if keep_with_next is not None:
                    try:
                        para_fmt.KeepWithNext = keep_with_next
                        results["keep_with_next"] = {
                            "status": "success",
                            "message": f"Set to {'on' if keep_with_next else 'off'}"
                        }
                    except Exception as e:
                        results["keep_with_next"] = {
                            "status": "error",
                            "message": f"Failed to set KeepWithNext: {str(e)}"
                        }

                if keep_together is not None:
                    try:
                        para_fmt.KeepTogether = keep_together
                        results["keep_together"] = {
                            "status": "success",
                            "message": f"Set to {'on' if keep_together else 'off'}"
                        }
                    except Exception as e:
                        results["keep_together"] = {
                            "status": "error",
                            "message": f"Failed to set KeepTogether: {str(e)}"
                        }

                if page_break_before is not None:
                    try:
                        para_fmt.PageBreakBefore = page_break_before
                        results["page_break_before"] = {
                            "status": "success",
                            "message": f"Set to {'on' if page_break_before else 'off'}"
                        }
                    except Exception as e:
                        results["page_break_before"] = {
                            "status": "error",
                            "message": f"Failed to set PageBreakBefore: {str(e)}"
                        }

        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to apply pagination control: {str(e)}"
            }

        # Save document (non-critical operation)
        try:
            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to save document: {str(e)}"
            }

        return results

    def set_spacing(self, doc, location_list, line_spacing=None, before_spacing=None, after_spacing=None):
        """
        Set spacing properties for multiple locations in a Word document

        Args:
            doc: Word document object
            location_list: List of positions (0 for current selection, positive integers for paragraph indices)
            line_spacing: Dictionary containing line spacing settings:
                - "rule": "single"/"1.5"/"double"/"exact"/"multiple"
                - "value": Value for exact/multiple spacing
            before_spacing: {"value": number, "unit": "pt"/"cm"/"in"/...}
            after_spacing: {"value": number, "unit": "pt"/"cm"/"in"/...}

        Returns:
            dict:
            {
                "line_spacing": {"status": str, "message": str},
                "before_spacing": {"status": str, "message": str},
                "after_spacing": {"status": str, "message": str},
                "error": {"status": "error", "message": "..."}  # optional
            }
        """
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        # Define spacing rule mapping
        rule_map = {
            "single": constants.wdLineSpaceSingle,
            "1.5x": constants.wdLineSpace1pt5,
            "double": constants.wdLineSpaceDouble,
            "exact": constants.wdLineSpaceExactly,
            "multiple": constants.wdLineSpaceMultiple
        }

        # Validate line spacing rule
        if line_spacing and line_spacing.get("rule") not in rule_map:
            results["line_spacing"] = {
                "status": "error",
                "message": f"Invalid line spacing rule: {line_spacing.get('rule')}"
            }
            return results

        try:
            for location in location_list:
                # Handle current selection (location = 0)
                if location == 0:
                    range_obj = doc.Application.Selection.Range
                elif location > 0:
                    if location > doc.Paragraphs.Count:
                        raise IndexError(f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})")
                    range_obj = doc.Paragraphs(location).Range
                else:
                    raise ValueError("Location must be non-negative integer")

                # Set spacing properties
                para_fmt = range_obj.ParagraphFormat

                # Line spacing
                if line_spacing:
                    try:
                        rule = line_spacing["rule"]
                        value = line_spacing.get("value", 1)

                        para_fmt.LineSpacingRule = rule_map[rule]

                        if rule == "exact":
                            spacing_value = float(value)
                            para_fmt.LineSpacing = spacing_value
                            results["line_spacing"] = {
                                "status": "success",
                                "message": f"Set to exact {spacing_value} pt"
                            }
                        elif rule == "multiple":
                            # spacing_value = float(value * 12)
                            spacing_value = float(value)
                            para_fmt.LineSpacing = spacing_value
                            results["line_spacing"] = {
                                "status": "success",
                                "message": f"Set to multiple ({value}x) {spacing_value} pt"
                            }
                        else:
                            results["line_spacing"] = {
                                "status": "success",
                                "message": f"Set to {rule} spacing"
                            }
                    except Exception as e:
                        results["line_spacing"] = {
                            "status": "error",
                            "message": f"Failed to set line spacing: {str(e)}"
                        }

                # Before spacing
                if before_spacing:
                    try:
                        val = before_spacing["value"]
                        unit = before_spacing.get("unit", "pt")
                        spacing_pt = self.convert_to_pt(val, unit)
                        para_fmt.SpaceBefore = spacing_pt

                        results["before_spacing"] = {
                            "status": "success",
                            "message": f"Set to {val} {unit} ({spacing_pt} pt)"
                        }
                    except Exception as e:
                        results["before_spacing"] = {
                            "status": "error",
                            "message": f"Failed to set before spacing: {str(e)}"
                        }

                # After spacing
                if after_spacing:
                    try:
                        val = after_spacing["value"]
                        unit = after_spacing.get("unit", "pt")
                        spacing_pt = self.convert_to_pt(val, unit)
                        para_fmt.SpaceAfter = spacing_pt

                        results["after_spacing"] = {
                            "status": "success",
                            "message": f"Set to {val} {unit} ({spacing_pt} pt)"
                        }
                    except Exception as e:
                        results["after_spacing"] = {
                            "status": "error",
                            "message": f"Failed to set after spacing: {str(e)}"
                        }

        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to apply spacing settings: {str(e)}"
            }

        # Save document (non-critical operation)
        try:
            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to save document: {str(e)}"
            }

        return results

    def set_indent(self, doc, location_list, left_indent=None, right_indent=None, firstline_indent=None):
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Paragraphs.Count)]
        # Unit conversion helper (kept as original)
        def to_point(value, unit, font_size=12):
            if unit in ["point", "pt", "cm", "mm", "inches"]:
                return self.convert_to_pt(value, unit)
            elif unit == "character":
                return font_size * value
            else:
                raise ValueError(f"Unsupported unit: {unit}")
        try:
            for location in location_list:
                try:
                    # Handle current selection (location = 0)
                    if location == 0:
                        range_obj = doc.Application.Selection.Range
                        font_size = range_obj.Font.Size
                    # Handle specified paragraph (location > 0)
                    elif location > 0:
                        if location > doc.Paragraphs.Count:
                            raise IndexError(
                                f"Paragraph index {location} exceeds document length ({doc.Paragraphs.Count})")
                        range_obj = doc.Paragraphs(location).Range
                        font_size = range_obj.Font.Size
                    else:
                        # 保持原逻辑的错误语义：位置必须为非负整数
                        raise ValueError("Location must be non-negative integer")

                    # Set indentation properties (kept original logic)
                    para_fmt = range_obj.ParagraphFormat

                    # Left indent
                    if left_indent:
                        try:
                            val = left_indent['value']
                            unit = left_indent.get('unit', 'pt')
                            pt_val = to_point(val, unit, font_size)
                            if left_indent.get('hanging', 0) == -1:
                                pt_val = -abs(pt_val)
                            para_fmt.LeftIndent = abs(pt_val)

                            indent_type = "Hanging" if pt_val < 0 else "Left"
                            results['left_indent'] = {
                                'status': 'success',
                                'message': f"{indent_type} indent set to {abs(pt_val)} pt (from {val} {unit})"
                            }
                        except Exception as e:
                            results['left_indent'] = {
                                'status': 'error',
                                'message': f"Failed to set left indent: {str(e)}"
                            }

                    # Right indent
                    if right_indent:
                        try:
                            val = right_indent['value']
                            unit = right_indent.get('unit', 'pt')
                            pt_val = to_point(val, unit, font_size)
                            if right_indent.get('hanging', 0) == -1:
                                pt_val = -abs(pt_val)
                            para_fmt.RightIndent = abs(pt_val)

                            indent_type = "Hanging" if pt_val < 0 else "Right"
                            results['right_indent'] = {
                                'status': 'success',
                                'message': f"{indent_type} indent set to {abs(pt_val)} pt (from {val} {unit})"
                            }
                        except Exception as e:
                            results['right_indent'] = {
                                'status': 'error',
                                'message': f"Failed to set right indent: {str(e)}"
                            }

                    # First line indent
                    if firstline_indent:
                        try:
                            val = firstline_indent['value']
                            unit = firstline_indent.get('unit', 'pt')
                            pt_val = to_point(val, unit, font_size)
                            if firstline_indent.get('hanging', 0) == -1:
                                pt_val = -abs(pt_val)
                            para_fmt.FirstLineIndent = abs(pt_val)

                            indent_type = "Hanging" if pt_val < 0 else "First line"
                            results['firstline_indent'] = {
                                'status': 'success',
                                'message': f"{indent_type} indent set to {abs(pt_val)} pt (from {val} {unit})"
                            }
                        except Exception as e:
                            results['firstline_indent'] = {
                                'status': 'error',
                                'message': f"Failed to set first line indent: {str(e)}"
                            }

                    # 继续处理下一个 location（保留按 location 逐个处理的行为）

                except Exception as e:
                    # 若某个 location 抛出错误（例如索引越界或 location 为负），把错误写入 results["error"] 并退出循环
                    results["error"] = {
                        'status': 'error',
                        'message': f"Error processing location: {str(e)}"
                    }
                    break

        except Exception as e:
            # 捕获外层意外错误
            results["error"] = {
                'status': 'error',
                'message': f"Failed to apply indent settings: {str(e)}"
            }

        # Save document (non-critical operation)
        try:
            doc.Save()
        except Exception as e:
            results["error"] = {
                'status': 'error',
                'message': f"Failed to save document: {str(e)}"
            }
        return results

class TextTools():
    def __init__(self):
        self.text_tool = BaseTextTools()
    def __set_base_font(self, doc, location_list, setting={}):
        result = self.text_tool.set_base_font(doc,location_list,setting)
        return result

    def __set_advanced_font(self, doc, location_list, setting={}):
        result = self.text_tool.set_advanced_font(doc, location_list, setting)
        return result

    def __set_outlinelevel(self, doc, location_list, settings = {}):
        outlinelevel = settings.get("outlinelevel",10)
        result = self.text_tool.set_outlinelevel(doc, location_list, outlinelevel=outlinelevel)
        return result

    def __set_alignment(self, doc, location_list, settings = {}):
        alignment = settings.get("alignment", "justify")
        result = self.text_tool.set_alignment(doc, location_list, alignment=alignment)
        return result

    def __set_pagination_control(self, doc, location_list, settings={}):
        widow_control = settings.get("widow_control",None)
        keep_with_next = settings.get("keep_with_next",None)
        keep_together = settings.get("keep_together",None)
        page_break_before = settings.get("page_break_before",None)
        result = self.text_tool.set_pagination_control(doc,location_list,widow_control=widow_control,
                                                       keep_with_next=keep_with_next,keep_together=keep_together,
                                                       page_break_before=page_break_before)
        return result


    def __set_spacing(self, doc, location_list, settings={}):
        line_spacing = settings.get("line_spacing",None)
        if "rule" not in line_spacing:
            line_spacing["rule"] = "exact"
        before_spacing_value = settings.get("before_spacing",None)
        after_spacing_value = settings.get("after_spacing",None)
        before_spacing, after_spacing= None, None
        if before_spacing_value:
            before_spacing = {"value":before_spacing_value,"unit":"pt"}
        if after_spacing_value:
            before_spacing = {"value":after_spacing_value,"unit":"pt"}
        result = self.text_tool.set_spacing(doc,location_list,line_spacing=line_spacing,
                                            before_spacing=before_spacing, after_spacing=after_spacing)
        return result

    def __set_indent(self, doc, location_list, settings={}):
        left_indent = None
        right_indent = None
        firstline_indent = None

        left_indent_value = settings.get("left_indent", None)
        right_indent_value = settings.get("right_indent", None)
        firstline_indent_value = settings.get("firstline_indent", None)

        if left_indent_value:
            left_indent = {
                "value": left_indent_value,
                "unit": "pt",
                "hanging": 0 if left_indent_value >0 else -1
            }
        if right_indent_value:
            right_indent = {
                "value": right_indent_value,
                "unit": "pt",
                "hanging": 0 if right_indent_value >0 else -1
            }
        if firstline_indent_value:
            firstline_indent = {
                "value": firstline_indent_value,
                "unit": "pt",
                "hanging": 0 if firstline_indent_value >0 else -1
            }
        result = self.text_tool.set_indent(doc, location_list,left_indent=left_indent,right_indent=right_indent,
                                           firstline_indent=firstline_indent)
        return result

    def set_format(self, doc, location_list, settings):
        support_functions = {
            "base_font": self.__set_base_font,
            "advanced_font": self.__set_advanced_font,
            "outlinelevel": self.__set_outlinelevel,
            "alignment": self.__set_alignment,
            "pagination_control": self.__set_pagination_control,
            "spacing": self.__set_spacing,
            "indent": self.__set_indent,
        }
        support_properties = list(support_functions.keys())
        for support_property in support_properties:
            property_setting = settings.get(support_property)
            if property_setting:
                property_function = support_functions.get(support_property)
                result = property_function(doc, location_list, property_setting)
                print(result)
