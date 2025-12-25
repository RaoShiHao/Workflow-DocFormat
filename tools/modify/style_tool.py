import os
from win32com.client import constants
import win32com.client as win32
import win32com
from tools.modify.tool_config import ContextToolsConfig

class StyleTools():
    def __init__(self, pyconfig=ContextToolsConfig("/config/Tools/StyleToolsConfig.yaml")):
        self.config = pyconfig.config
        self.name = self.config.get("name")

    def convert_to_pt(self, value, unit):
        execl = win32com.client.Dispatch("Excel.Application")
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


    def pt_to_convert(self, value, unit):
        value = float(value)
        execl = win32com.client.Dispatch("Excel.Application")
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit == "pt" or unit == "point":
            return value
        elif unit == "cm":
            return round(value/execl.CentimetersToPoints(1),2)
        elif unit == "mm":
            return round(value/execl.CentimetersToPoints(0.1),2)
        elif unit == "inches":
            return round(value/execl.InchesToPoints(1),2)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    # - "NameFarEast": 中文字体名称(str)，单独设置中文字体，仅影响中文等东亚字符的显示样式，在强调字符区分时启用。

    def set_basic_font(self, doc, style_name, setting={},**kwargs):
        """
        修改或创建 Word 中指定段落样式的字体属性设置。
        所有使用该样式的段落将继承这些字体设置。
        """
        results = {}

        # 获取或创建样式
        try:
            style = doc.Styles(style_name)
        except Exception as e:
            style = doc.Styles.Add(style_name, constants.wdStyleTypeParagraph)

        font = style.Font

        # 设置字体属性
        # for attr in ["Name", "Size","NameFarEast", "NameAscii", "Bold", "Italic", "Underline"]:
        for attr in ["Name", "Size", "NameAscii",  "Bold", "Italic", "Underline"]:
            if attr in setting:
                try:
                    setattr(font, attr, setting[attr])
                    results[attr] = {
                        "status": "success",
                        "message": f"{attr} set to {setting[attr]}"
                    }
                except Exception as e:
                    results[attr] = {
                        "status": "error",
                        "message": f"Failed to set {attr}: {str(e)}"
                    }

        # 设置颜色
        if "Color" in setting and setting["Color"]:
            try:
                hex_color = setting["Color"].lstrip("#")
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                rgb_color = r * 65536 + g * 256 + b
                font.Color = rgb_color
                results["Color"] = {
                    "status": "success",
                    "message": f"Color set to {setting['Color']}"
                }
            except Exception as e:
                results["Color"] = {
                    "status": "error",
                    "message": f"Failed to set Color: {str(e)}"
                }

        # 设置为快速样式
        style.QuickStyle = True
        # 保存文档
        doc.Save()
        return results


    def set_font_effects(self, doc, style_name, setting={},**kwargs):
        """
        使用样式统一设置多个段落的高级字体效果。如果样式不存在则创建。
        每一项设置都捕获异常并返回详细结果。
        """
        results = {}

        try:
            # 检查或创建样式
            styles = doc.Styles
            try:
                style = styles(style_name)
            except Exception as e:
                style = styles.Add(style_name, 1)  # 1: wdStyleTypeParagraph

            font = style.Font

            # 定义所有支持的字体效果字段
            effect_fields = [
                "StrikeThrough", "Subscript", "Superscript",
                "AllCaps", "SmallCaps", "Spacing",
                "Scaling", "Emboss", "Engrave", "Shadow"
            ]

            for field in effect_fields:
                if field in setting:
                    try:
                        setattr(font, field, setting[field])
                        results[field] = {
                            "status": "success",
                            "message": f"{field} set to {setting[field]}"
                        }
                    except Exception as e:
                        results[field] = {
                            "status": "error",
                            "message": f"Failed to set {field}: {str(e)}"
                        }

            # 设置为快速样式并保存
            style.QuickStyle = True
            doc.Save()

        except Exception as e:
            results["exception"] = str(e)

        return results


    def set_outline_level(self, doc, style_name, outlinelevel,**kwargs):
        """
        批量设置多个段落的大纲级别
        - 1-9: 对应 Word 的标题级别（1为最高级，9为最低级）
        - 10: 将段落设置为普通正文文本（无大纲级别）
        """
        results = {}

        # 解析 outlinelevel 参数
        try:
            outlinelevel = int(outlinelevel)
            if outlinelevel < 1 or outlinelevel > 10:
                raise ValueError("outlinelevel 必须是 1-9（标题级别）或 10（正文文本）")
            results["outlinelevel_validation"] = {
                "status": "success",
                "message": f"Outline level validated as {outlinelevel}"
            }
        except Exception as e:
            results["outlinelevel_validation"] = {
                "status": "error",
                "message": f"Invalid outline level: {str(e)}"
            }
            return results  # 不再继续执行

        # 尝试获取或创建样式
        try:
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]
            if style_name in style_names:
                style = styles(style_name)
            else:
                style = styles.Add(style_name, win32.constants.wdStyleTypeParagraph)
        except Exception as e:
            results["paragraph"] = {
                "status": "error",
                "message": f"Failed to get or create paragraph: {str(e)}"
            }
            return results

        # 设置段落格式的大纲级别
        try:
            para_fmt = style.ParagraphFormat
            para_fmt.OutlineLevel = outlinelevel
            results["OutlineLevel"] = {
                "status": "success",
                "message": f"OutlineLevel set to {outlinelevel}"
            }
        except Exception as e:
            results["OutlineLevel"] = {
                "status": "error",
                "message": f"Failed to set OutlineLevel: {str(e)}"
            }

        # 设置快速样式（不捕获异常）
        style.QuickStyle = True
        doc.Save()

        return results


    def set_alignment(self, doc, style_name, alignment,**kwargs):
        """
        设置指定样式的段落对齐方式：
        - left：左对齐，段落靠左边排列
        - center：居中对齐，段落在页面中居中
        - right：右对齐，段落靠右边排列
        - justify：两端对齐，段落左右两端同时对齐
        - distribute：分散对齐，自动调整字符间距使各行长度相等
        """
        results = {}

        # 对齐方式映射
        try:
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

            if alignment not in alignment_map:
                raise ValueError(f"无效的对齐方式：{alignment}")

            alignment_value = alignment_map[alignment]

        except Exception as e:
            results["alignment_validation"] = {
                "status": "error",
                "message": f"Invalid alignment value: {str(e)}"
            }
            return results

        # 获取或创建样式
        try:
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]

            if style_name in style_names:
                style = styles(style_name)
            else:
                style = styles.Add(style_name, constants.wdStyleTypeParagraph)
        except Exception as e:
            results["paragraph"] = {
                "status": "error",
                "message": f"Failed to get or create paragraph: {str(e)}"
            }
            return results

        # 设置段落对齐方式
        try:
            para_fmt = style.ParagraphFormat
            para_fmt.Alignment = alignment_value
            results["alignment"] = {
                "status": "success",
                "message": f"Paragraph alignment set to {alignment}"
            }
        except Exception as e:
            results["alignment"] = {
                "status": "error",
                "message": f"Failed to set alignment: {str(e)}"
            }

        # 设置快速样式和保存（不加 try-catch）
        style.QuickStyle = True
        doc.Save()

        return results


    def set_pagination_control(self, doc, style_name, widow_control=None, keep_with_next=None, keep_together=None,
                               page_break_before=None,**kwargs):
        """
        设置指定样式的分页属性：
        - widow_control: 防止孤行（段落首尾行单独出现在页面边缘）
        - keep_with_next: 与下段同页
        - keep_together: 段中不分页
        - page_break_before: 段前分页
        """
        results = {}
        try:
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]

            if style_name in style_names:
                style = styles(style_name)
            else:
                style = styles.Add(style_name, constants.wdStyleTypeParagraph)
            para_fmt = style.ParagraphFormat

            if widow_control is not None:
                try:
                    para_fmt.WidowControl = widow_control
                    results["widow_control"] = {
                        "status": "success",
                        "message": f"WidowControl set to {widow_control}"
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
                        "message": f"KeepWithNext set to {keep_with_next}"
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
                        "message": f"KeepTogether set to {keep_together}"
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
                        "message": f"PageBreakBefore set to {page_break_before}"
                    }
                except Exception as e:
                    results["page_break_before"] = {
                        "status": "error",
                        "message": f"Failed to set PageBreakBefore: {str(e)}"
                    }

            # 快速样式和保存，不做异常捕获
            style.QuickStyle = True
            doc.Save()

            return results

        except Exception as e:
            return {
                "error": {
                    "status": "fatal",
                    "message": f"Unexpected error occurred: {str(e)}"
                }
            }


    def set_spacing(self, doc, style_name, line_spacing=None, before_spacing=None, after_spacing=None,**kwargs):
        """
        设置段落样式的行间距、段前间距、段后间距。
        每项设置提供独立反馈信息。
        """
        results = {}

        try:
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]

            if style_name in style_names:
                style = styles(style_name)
            else:
                style = styles.Add(style_name, constants.wdStyleTypeParagraph)

            para_fmt = style.ParagraphFormat

            # 行间距设置
            if line_spacing:
                try:
                    rule = line_spacing.get("spacing_rule")
                    value = line_spacing.get("value", 1)

                    rule_map = {
                        "single": 0,  # wdLineSpaceSingle
                        "1.5": 1,  # wdLineSpace1pt5
                        "double": 2,  # wdLineSpaceDouble
                        "exact": 4,  # wdLineSpaceExactly
                        "multiple": 5,  # wdLineSpaceMultiple
                        "单倍": 0,  # wdLineSpaceSingle
                        "1.5倍": 1,  # wdLineSpace1pt5
                        "双倍": 2,  # wdLineSpaceDouble
                        "固定值": 4,  # wdLineSpaceExactly
                        "多倍": 5  # wdLineSpaceMultiple
                    }

                    if rule not in rule_map:
                        results["feedback"] = {
                            "status": "error",
                            "message": f"无效的行间距规则: {rule}!\n ValueError in pacing_rule!"
                        }
                        return results

                    para_fmt.LineSpacingRule = rule_map[rule]

                    if rule == "exact":
                        para_fmt.LineSpacing = float(value)
                        results["line_spacing"] = {
                            "status": "success",
                            "message": f"Line spacing set with rule '{rule}' and value '{value}'"
                        }
                    elif rule == "multiple":
                        para_fmt.LineSpacing = float(value * 12)
                        results["line_spacing"] = {
                            "status": "success",
                            "message": f"Line spacing set with rule '{rule}' and value '{value}'"
                        }
                    else:
                        results["line_spacing"] = {
                            "status": "success",
                            "message": f"Line spacing set with rule '{rule}'"
                        }
                        pass
                except Exception as e:
                    results["line_spacing"] = {
                        "status": "error",
                        "message": f"Failed to set line spacing: {str(e)}"
                    }

            # 段前间距设置
            if before_spacing:
                try:
                    val = before_spacing.get("value")
                    unit = before_spacing.get("unit", "pt")
                    pt_val = self.convert_to_pt(val, unit)
                    para_fmt.SpaceBefore = pt_val
                    results["before_spacing"] = {
                        "status": "success",
                        "message": f"Space before set to {pt_val} pt (from {val} {unit})"
                    }
                except Exception as e:
                    results["before_spacing"] = {
                        "status": "error",
                        "message": f"Failed to set space before: {str(e)}"
                    }

            # 段后间距设置
            if after_spacing:
                try:
                    val = after_spacing.get("value")
                    unit = after_spacing.get("unit", "pt")
                    pt_val = self.convert_to_pt(val, unit)
                    para_fmt.SpaceAfter = pt_val
                    results["after_spacing"] = {
                        "status": "success",
                        "message": f"Space after set to {pt_val} pt (from {val} {unit})"
                    }
                except Exception as e:
                    results["after_spacing"] = {
                        "status": "error",
                        "message": f"Failed to set space after: {str(e)}"
                    }

            # 设置快速样式与保存（不包 try）
            style.QuickStyle = True
            doc.Save()

            return results

        except Exception as e:
            return {
                "error": {
                    "status": "fatal",
                    "message": f"Unexpected error occurred: {str(e)}"
                }
            }


    def set_indent(self, doc, style_name, left_indent=None, right_indent=None, firstline_indent=None,**kwargs):
        """
        设置指定段落样式的左缩进、右缩进、首行缩进或悬挂缩进
        返回每一项设置的状态信息。
        """
        results = {}
        try:
            # 单位转换函数
            def to_point(value, unit, font_size=12):
                if unit in ["point", "pt", "cm", "mm", "inches"]:
                    return self.convert_to_pt(value, unit)
                elif unit == "character":
                    return font_size * value
                else:
                    raise ValueError(f"Unsupported unit: {unit}")

            # 获取或新建样式
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]
            if style_name in style_names:
                style = styles(style_name)
            else:
                style = styles.Add(style_name, constants.wdStyleTypeParagraph)

            para_fmt = style.ParagraphFormat
            font_size = style.Font.Size

            # 设置左缩进
            if left_indent:
                try:
                    val = left_indent['value']
                    unit = left_indent.get('unit', 'pt')
                    pt_val = to_point(val, unit, font_size)
                    if left_indent.get('hanging', 0) == -1:
                        pt_val = -abs(pt_val)
                    para_fmt.LeftIndent = abs(pt_val)
                    results["left_indent"] = {
                        "status": "success",
                        "message": f"Left indent set to {abs(pt_val)} pt (from {val} {unit})"
                    }
                except Exception as e:
                    results["left_indent"] = {
                        "status": "error",
                        "message": f"Failed to set left indent (from {val} {unit}): {str(e)}"
                    }

            # 设置右缩进
            if right_indent:
                try:
                    val = right_indent['value']
                    unit = right_indent.get('unit', 'pt')
                    pt_val = to_point(val, unit, font_size)
                    if right_indent.get('hanging', 0) == -1:
                        pt_val = -abs(pt_val)
                    para_fmt.RightIndent = abs(pt_val)
                    results["right_indent"] = {
                        "status": "success",
                        "message": f"Right indent set to {abs(pt_val)} pt (from {val} {unit})"
                    }
                except Exception as e:
                    results["right_indent"] = {
                        "status": "error",
                        "message": f"Failed to set right indent (from {val} {unit}): {str(e)}"
                    }

            # 设置首行缩进或悬挂缩进
            if firstline_indent:
                try:
                    val = firstline_indent['value']
                    unit = firstline_indent.get('unit', 'pt')
                    pt_val = to_point(val, unit, font_size)
                    if firstline_indent.get('hanging', 0) == -1:
                        pt_val = -abs(pt_val)
                    para_fmt.FirstLineIndent = abs(pt_val)
                    results["firstline_indent"] = {
                        "status": "success",
                        "message": f"First line indent set to {abs(pt_val)} pt (from {val} {unit})"
                    }

                except Exception as e:
                    results["firstline_indent"] = {
                        "status": "error",
                        "message": f"Failed to set first line indent (from {val} {unit}): {str(e)}"
                    }

            # 添加为快速样式
            style.QuickStyle = True
            doc.Save()
            return results

        except Exception as e:
            results["FatalError"] = {
                "status": "error",
                "message": str(e)
            }
            return results


    def set_paragraph(self,doc,style_name,paragraph_settings,**kwargs):
        if "outlinelevel" in paragraph_settings:
            self.set_outline_level(doc,style_name,paragraph_settings.get("outlinelevel"))
        if "alignment" in paragraph_settings:
            self.set_alignment(doc,style_name,paragraph_settings.get("alignment"))

        spacing_settings = {
            "line_spacing":paragraph_settings.get("line_spacing",None),
            "before_spacing":paragraph_settings.get(" before_spacing",None),
            "after_spacing":paragraph_settings.get("after_spacing",None)
        }
        self.set_spacing(doc,style_name,**spacing_settings)

        indent_settings = {
        "left_indent":paragraph_settings.get("left_indent",None),
        "right_indent":paragraph_settings.get("right_indent",None),
        "firstline_indent":paragraph_settings.get("firstline_indent",None)
        }

        self.set_indent(doc, style_name, **indent_settings)

        pagination_settings = {
            "widow_control":paragraph_settings.get("widow_control",None),
            "keep_with_next":paragraph_settings.get("keep_with_next",None),
            "keep_together":paragraph_settings.get("keep_together",None),
            "page_break_before":paragraph_settings.get("page_break_before",None)
        }
        self.set_pagination_control(doc,style_name,**pagination_settings)


    def apply_style(self, doc, style_name, paragraph_list):
        try:
            # 获取或新建样式
            styles = doc.Styles
            style_names = [s.NameLocal for s in styles]
            if style_name in style_names:
                style = styles(style_name)
            else:
                print("No such paragraph, Pass")
                raise ValueError
            for para_index in paragraph_list:
                paragraph = doc.Paragraphs(para_index)
                paragraph.Range.Font.Reset()
                paragraph.Range.ParagraphFormat.Reset()
                paragraph.Range.Style = styles("正文")
                paragraph.Range.Style = style
            doc.Save()
        except Exception as e:
            print(f"Style Apply Error! The detail is {e}")


    def set_style(self, doc, style_name, paragraph_setting = {}, base_font_setting = {}, advance_font_setting={},**kwargs):
        if base_font_setting:
            self.set_basic_font(doc,style_name,base_font_setting)
        if advance_font_setting:
            self.set_font_effects(doc,style_name,advance_font_setting)
        if paragraph_setting:
            self.set_paragraph(doc,style_name,paragraph_setting)




