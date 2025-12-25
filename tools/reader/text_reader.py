from win32com.client import constants
import copy
from tools.modify.tool_config import ContextToolsConfig
class TextReader():
    def __init__(self, pyconfig=ContextToolsConfig(config_path="config/reader/text_reader_config.yaml")):
        self.config = pyconfig.config

    def pt_to_convert(self, value, unit):
        value = float(value)
        # 加速运算，直接读取换算值
        # execl = win32com.client.Dispatch("Excel.Application")
        # cm_unit = execl.CentimetersToPoints(1)
        # inches_unit = execl.InchesToPoints(1)
        cm_unit = 28.346456692913385
        inches_unit = 72.0
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit == "pt" or unit == "point":
            return value
        elif unit == "cm":
            return round(value/cm_unit,2)
        elif unit == "mm":
            return round(10*value/cm_unit,2)
        elif unit == "inches":
            return round(value/inches_unit,2)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def color_int_to_hex(self, color_int: int) -> str:
        # 提取BGR分量
        b = (color_int >> 16) & 0xFF  # 蓝色
        g = (color_int >> 8) & 0xFF  # 绿色
        r = color_int & 0xFF  # 红色
        # 组合为RGB
        return "#{:02X}{:02X}{:02X}".format(r, g, b)

    def __get_base_font_info(self,range_obj,*args,**kwargs):
        font = range_obj.Font
        return {
            "Name": font.Name,  # 字体名称
            "NameAscii": font.NameAscii,  # 西文字体
            "Size": font.Size,  # 字体大小
            "Bold": font.Bold,  # 是否加粗
            "Italic": font.Italic,  # 是否倾斜
            "Underline": font.Underline,  # 下划线样式
            "Color": self.color_int_to_hex(font.Color),  # 字体颜色
            "HighlightColor": self.color_int_to_hex(font.Shading.BackgroundPatternColor),  # 背景高亮颜色
        }

    def __get_advanced_font_info(self,range_obj,*args,**kwargs):
        font = range_obj.Font
        return {
            "StrikeThrough": font.StrikeThrough,  # 是否删除线
            "Subscript": font.Subscript,  # 是否下标
            "Superscript": font.Superscript,  # 是否上标
            "AllCaps": font.AllCaps,  # 是否全部大写
            "SmallCaps": font.SmallCaps,  # 是否小型大写
            "Spacing": font.Spacing,  # 字符间距
            "Scaling": font.Scaling,  # 字符缩放比例
            "Emboss": font.Emboss,  # 是否浮雕效果
            "Engrave": font.Engrave,  # 是否雕刻效果
            "Shadow": font.Shadow,  # 是否阴影效果
        }

    def __get_outlinelevel_info(self, range, *args,**kwargs):
        fmt = range.ParagraphFormat
        return {
            # 大纲级别
            "outlinelevel": fmt.OutlineLevel,  # 大纲级别（1-10）
        }

    def __get_alignment_info(self, range_obj, *args, **kwargs):
        fmt = range_obj.ParagraphFormat
        align_key = {
            0: "left", 1: "center", 2: "right",
            3: "justify", 4: "distribute"
        }.get(fmt.Alignment, "unknown")

        return {
            # 对齐方式
            "alignment": align_key,
        }

    def __get_pagination_control_info(self,  range_obj, *args, **kwargs):
        fmt = range_obj.ParagraphFormat
        return {
                "widow_control": fmt.WidowControl,  # 孤行控制
                "keep_with_next": fmt.KeepWithNext,  # 与下段同页
                "keep_together": fmt.KeepTogether,  # 段中不分页
                "page_break_before": fmt.PageBreakBefore  # 段前分页
        }

    def __get_spacing_info(self,  range_obj, *args, **kwargs):
        fmt = range_obj.ParagraphFormat
        return {
                # 行间距
                "line_spacing": {
                    "value": fmt.LineSpacing,
                    "rule": {
                        0: "single", 1: "1.5x", 2: "double",
                        4: "exact", 5: "multiple"
                    }.get(fmt.LineSpacingRule, "custom")
                },
                "before_spacing": fmt.SpaceBefore,  # 段前间距（磅）
                "after_spacing": fmt.SpaceAfter,  # 段后间距（磅）
        }

    def __get_indent_info(self, range_obj, *args, **kwargs):

        fmt = range_obj.ParagraphFormat
        return {
                "left_indent": fmt.LeftIndent,  # 左缩进（磅）
                "right_indent": fmt.RightIndent,  # 右缩进（磅）
                "firstline_indent": fmt.FirstLineIndent  # 首行缩进（正数）或悬挂缩进（负数）
        }

    def get_paragraphs_format(self,doc):
        formats = []
        paragraph_num = doc.Paragraphs.Count
        for index in range(paragraph_num):
            paragraph_index = index+1
            format = self.get_format(doc,paragraph_index)
            if format.get("state") == "success":
                style_name = doc.Paragraphs(paragraph_index).Range.Text
                style_name = style_name.replace("\r","")
                settings = format.get("properties")
                formats.append({"style_name":style_name,"paragraph_list":[paragraph_index],"format_properties":settings})
        return formats

    def get_format(self, doc, paragraph_index, *args,**kwargs):
        properties = {}
        attribution_dict = {
            "base_font": self.__get_base_font_info,
            "advanced_font": self.__get_advanced_font_info,
            "outlinelevel": self.__get_outlinelevel_info,
            "alignment": self.__get_alignment_info,
            "pagination_control": self.__get_pagination_control_info,
            "spacing": self.__get_spacing_info,
            "indent": self.__get_indent_info
        }
        try:
            if paragraph_index == 0:
                range_obj = doc.Application.Selection.Range
            elif paragraph_index >0:
                range_obj = doc.Paragraphs(paragraph_index).Range
            else:
                print("paragraph index must >= 0!")
                raise

            for attribution,get_property_function in attribution_dict.items():
                properties[attribution] = get_property_function(range_obj)
            # 返回成功结果
            return {"state": "success", "properties": properties}
        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "exception": str(e)}

    def get_paragraph_info(self, doc, index, key_properties = 'all'):
        """
        获取指定段落的全部格式属性
        :param index: 段落的索引（从 1 开始）
        :return: 包含段落属性的字典（若失败返回错误信息）
        """
        try:
            # 检查索引有效性
            if index < 1 or index > doc.Paragraphs.Count:
                raise ValueError(f"段落索引 {index} 超出有效范围（1-{doc.Paragraphs.Count}")
            # 获取段落对象
            paragraph = doc.Paragraphs(index)
            font = paragraph.Range.Font
            fmt = paragraph.Range.ParagraphFormat
            # 获取段落的开始页码（起始位置）
            start_page = paragraph.Range.Information(3)
            # 获取段落的结束页码（结束位置）
            end_range = paragraph.Range.Duplicate  # 复制Range对象避免修改原段落
            end_range.Collapse(Direction=constants.wdCollapseEnd)  # 折叠到段落末尾
            # end_range.Collapse(Direction=0)  # 折叠到段落末尾
            end_page = end_range.Information(3)

            is_table = paragraph.Range.Tables.Count
            if is_table:
                return {"state": "success","properties": None,}

            if key_properties == 'all':
                # 构建属性字典
                properties = {
                    "index": index,
                    # 基础属性
                    "text": paragraph.Range.Text.strip(),
                    "style": paragraph.Style.NameLocal,
                    # range page跨页
                    "page_range": {"start_page": start_page, "end_page": end_page},
                    # 大纲级别
                    "outlinelevel": fmt.OutlineLevel,  # 大纲级别（1-10）
                    "font": {
                        "bold": font.Bold == -1,
                        "name": font.Name,
                        "size": font.Size
                    }
                }
            else:
                properties = {
                "index": index,
                # 基础属性
                "text": paragraph.Range.Text.strip(),
                "page_range": {"start_page": start_page, "end_page": end_page},
            }
            return {
                "state": "success",
                "properties": properties,
            }
        except Exception as e:
            return {
                "state": "false",
                "properties": None,
                "exception": str(e)
            }

    def __read_base_font_info(self, range_obj, text_info, *args, **kwargs):
        font_fmt = range_obj.Font
        text_info["base_font"]["NameAscii"]["value"] = font_fmt.NameAscii
        text_info["base_font"]["Name"]["value"] = font_fmt.Name
        text_info["base_font"]["Size"]["value"] = font_fmt.Size
        text_info["base_font"]["Bold"]["value"] = font_fmt.Bold
        text_info["base_font"]["Italic"]["value"] = font_fmt.Italic
        text_info["base_font"]["Underline"]["value"] = font_fmt.Underline
        text_info["base_font"]["Color"]["value"] = self.color_int_to_hex(font_fmt.Color)
        text_info["base_font"]["HighlightColorIndex"]["value"] = range_obj.HighlightColorIndex
        return text_info

    def __read_advanced_font_info(self, range_obj, text_info, *args, **kwargs):
        font_fmt = range_obj.Font
        # 10 advanced_font font properties
        text_info["advanced_font"]["StrikeThrough"]["value"] = font_fmt.StrikeThrough
        text_info["advanced_font"]["Subscript"]["value"] = font_fmt.Subscript
        text_info["advanced_font"]["Superscript"]["value"] = font_fmt.Superscript
        text_info["advanced_font"]["AllCaps"]["value"] = font_fmt.AllCaps
        text_info["advanced_font"]["Spacing"]["value"] = font_fmt.Spacing
        text_info["advanced_font"]["Scaling"]["value"] = font_fmt.Scaling
        text_info["advanced_font"]["Emboss"]["value"] = font_fmt.Emboss
        text_info["advanced_font"]["Engrave"]["value"] = font_fmt.Engrave
        text_info["advanced_font"]["Shadow"]["value"] = font_fmt.Shadow
        text_info["advanced_font"]["SmallCaps"]["value"] = font_fmt.SmallCaps
        return text_info


    def __read_outlinelevel_info(self, range_obj, text_info, *args, **kwargs):

        para_fmt = range_obj.ParagraphFormat
        text_info['outlinelevel']["value"] = para_fmt.OutlineLevel
        return text_info

    def __read_alignment_info(self, range_obj, text_info, *args, **kwargs):
        para_fmt = range_obj.ParagraphFormat
        # paragraph properties
        align_key = {
            0: "left", 1: "center", 2: "right",
            3: "justify", 4: "distribute"
        }.get(para_fmt.Alignment, "unknown")
        text_info['alignment']["value"] = align_key
        return text_info

    def __read_pagination_control_info(self, range_obj, text_info, *args, **kwargs):

        para_fmt = range_obj.ParagraphFormat
        # pagination_control
        text_info["pagination_control"]['widow_control']['value'] = para_fmt.WidowControl
        text_info["pagination_control"]['keep_with_next']['value'] = para_fmt.KeepWithNext
        text_info["pagination_control"]['keep_together']['value'] = para_fmt.KeepTogether
        text_info["pagination_control"]['page_break_before']['value'] = para_fmt.PageBreakBefore
        return text_info

    def __read_spacing_info(self, range_obj, text_info, *args, **kwargs):
        para_fmt = range_obj.ParagraphFormat

        # spacing
        text_info["spacing"]['line_spacing']["spacing_rule"]['value'] = {
            0: "single", 1: "1.5x", 2: "double",
            4: "exact", 5: "multiple"
        }.get(para_fmt.LineSpacingRule, "custom")
        text_info["spacing"]['line_spacing']["spacing_value"]['value'] = para_fmt.LineSpacing

        space_before = para_fmt.SpaceBefore
        text_info["spacing"]['before_spacing']['value']["pt"] = self.pt_to_convert(space_before, "pt")
        text_info["spacing"]['before_spacing']['value']["cm"] = self.pt_to_convert(space_before, "cm")
        text_info["spacing"]['before_spacing']['value']["mm"] = self.pt_to_convert(space_before, "mm")
        text_info["spacing"]['before_spacing']['value']["inches"] = self.pt_to_convert(space_before, "inches")
        space_after = para_fmt.SpaceAfter
        text_info["spacing"]['after_spacing']['value']["pt"] = self.pt_to_convert(space_after, "pt")
        text_info["spacing"]['after_spacing']['value']["cm"] = self.pt_to_convert(space_after, "cm")
        text_info["spacing"]['after_spacing']['value']["mm"] = self.pt_to_convert(space_after, "mm")
        text_info["spacing"]['after_spacing']['value']["inches"] = self.pt_to_convert(space_after, "inches")

        return text_info

    def __read_indent_info(self, range_obj, text_info, *args, **kwargs):

        # indent
        character_unit = range_obj.Font.Size
        indent = self.__get_indent_info(range_obj)
        left_indent = indent.get("left",0)
        right_indent = indent.get("right",0)
        firstline_indent = indent.get("first_line",0)

        if left_indent >= 0:
            text_info["indent"]['left_indent']['hanging'] = 0
        else:
            text_info["indent"]['left_indent']['hanging'] = -1
        left_indent = abs(left_indent)
        text_info["indent"]['left_indent']['value']["pt"] = self.pt_to_convert(left_indent, "pt")
        text_info["indent"]['left_indent']['value']["cm"] = self.pt_to_convert(left_indent, "cm")
        text_info["indent"]['left_indent']['value']["mm"] = self.pt_to_convert(left_indent, "mm")
        text_info["indent"]['left_indent']['value']["inches"] = self.pt_to_convert(left_indent, "inches")
        text_info["indent"]['left_indent']['value']["character"] = round(left_indent / character_unit)

        if right_indent >= 0:
            text_info["indent"]['right_indent']['hanging'] = 0
        else:
            text_info["indent"]['right_indent']['hanging'] = -1

        right_indent = abs(right_indent)
        text_info["indent"]['right_indent']['value']["pt"] = self.pt_to_convert(right_indent, "pt")
        text_info["indent"]['right_indent']['value']["cm"] = self.pt_to_convert(right_indent, "cm")
        text_info["indent"]['right_indent']['value']["mm"] = self.pt_to_convert(right_indent, "mm")
        text_info["indent"]['right_indent']['value']["inches"] = self.pt_to_convert(right_indent, "inches")
        text_info["indent"]['right_indent']['value']["character"] = round(right_indent / character_unit)

        if firstline_indent >= 0:
            text_info["indent"]['firstline_indent']['hanging'] = 0
        else:
            text_info["indent"]['firstline_indent']['hanging'] = -1
        firstline_indent = abs(firstline_indent)
        text_info["indent"]['firstline_indent']['value']["pt"] = self.pt_to_convert(firstline_indent,
                                                                                    "pt")
        text_info["indent"]['firstline_indent']['value']["cm"] = self.pt_to_convert(firstline_indent,
                                                                                    "cm")
        text_info["indent"]['firstline_indent']['value']["mm"] = self.pt_to_convert(firstline_indent,
                                                                                    "mm")
        text_info["indent"]['firstline_indent']['value']["inches"] = self.pt_to_convert(
            firstline_indent, "inches")
        text_info["indent"]['firstline_indent']['value']["character"] = round(
            firstline_indent / character_unit)

        return text_info

    def read_text_properties(self, doc, paragraph_index, params_list=[], language='zh', *args, **kwargs):
        # print(params_list)
        attribution_dict = {
            "base_font": self.__read_base_font_info,
            "advanced_font": self.__read_advanced_font_info,
            "outlinelevel": self.__read_outlinelevel_info,
            "alignment": self.__read_alignment_info,
            "pagination_control": self.__read_pagination_control_info,
            "spacing": self.__read_spacing_info,
            "indent": self.__read_indent_info,
        }
        # 加载读取模板
        template = self.config.get("properties_template")
        if language in ['zh', 'en']:
            text_info = copy.deepcopy(template.get(language))
        else:
            text_info = copy.deepcopy(template.get("zh"))
            print("Default Using Chinese")
        try:
            # 字符串转换
            paragraph_index = int(paragraph_index)
            if paragraph_index == 0:
                range_obj = doc.Application.Selection.Range
            elif paragraph_index > 0:
                range_obj = doc.Paragraphs(paragraph_index).Range
            else:
                print("paragraph index must >= 0!")
                raise
            if not params_list:
                # 未指定读取属性范围，默认全都要读取
                params_list = list(attribution_dict.keys())
            else:
                # 指定读取属性范围以后，删除模板中不需要读取的键值对
                for attribution in attribution_dict.keys():
                    if attribution not in params_list:
                        text_info.pop(attribution)

            # 依次获取要读取的属性
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    attribution_info_read_tool = attribution_dict.get(params)
                    text_info = attribution_info_read_tool(range_obj, text_info)

            # 返回成功结果
            return {"state": "success", "properties": text_info}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "paragraph_index": paragraph_index, "exception": str(e)}



if __name__ == '__main__':
    from constant import ABS_DIR
    import os
    import win32com.client as win32
    word = win32.DispatchEx("Word.Application")
    word.Visible = True  # 设为可见（调试时建议开启）
    word_file_path = "file/template.docx"

    word_file_path = os.path.join(ABS_DIR, word_file_path)
    # 打开已有文档
    try:
        # 打开文档
        doc = word.Documents.Open(word_file_path)
        print(doc.Paragraphs(2).Range.Font.Color)
        # reader_tool = TextReader()

        # print(reader_tool.get_paragraphs_format(doc))

    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        # 确保清理资源
        if 'doc' in locals():
            doc.Close(SaveChanges=False)
        word.Quit()
