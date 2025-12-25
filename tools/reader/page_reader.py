import win32com.client as win32
import copy
from tools.modify.tool_config import ContextToolsConfig
class PageReader():
    def __init__(self, pyconfig=ContextToolsConfig(config_path="config/reader/page_reader_config.yaml")):
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

    def get_page_info(self, doc, content_x):
        page_info_result = []
        for section_index in range(1, doc.Sections.Count + 1):
            section_page_result = self.__get_section_info(doc,section_index=section_index, content_x=content_x)
            if section_page_result.get("state") == "success":
                section_page_result.pop("state")
                page_info_result.append(section_page_result)
        return page_info_result

    def get_page_styles(self, doc):
        # 存储最终的页面样式结果
        page_style_result = []
        # 遍历所有章节
        for section_index in range(1, doc.Sections.Count + 1):
            section_page_result = self.__get_section_properties(doc, section_index=section_index)

            if section_page_result.get("state") == "success":
                # 移除state字段
                section_page_result.pop("state")
                # 检查是否已存在相同的格式属性
                found = False
                for item in page_style_result:
                    # 比较两个字典的所有字段是否相等
                    if self._are_dicts_equal(item["format_properties"], section_page_result):
                        # 如果格式属性相同，将当前章节索引添加到section_list中
                        item["section_list"].append(section_index)
                        found = True
                        break

                # 如果没有找到相同的格式属性，创建新的条目
                if not found:
                    page_style_result.append({
                        "section_list": [section_index],
                        "format_properties": section_page_result.copy().get('properties')  # 使用副本避免引用问题
                    })

        return page_style_result

    def _are_dicts_equal(self, dict1, dict2):
        """
        比较两个字典是否完全相等
        """
        # 如果键的数量不同，直接返回False
        if len(dict1) != len(dict2):
            return False

        # 遍历所有键值对进行比较
        for key, value1 in dict1.items():
            # 如果dict2中没有该键，返回False
            if key not in dict2:
                return False

            value2 = dict2[key]

            # 如果值都是字典，递归比较
            if isinstance(value1, dict) and isinstance(value2, dict):
                if not self._are_dicts_equal(value1, value2):
                    return False
            # 如果值都是列表，比较列表内容
            elif isinstance(value1, list) and isinstance(value2, list):
                if len(value1) != len(value2):
                    return False
                for i in range(len(value1)):
                    if value1[i] != value2[i]:
                        return False
            # 其他类型直接比较
            else:
                if value1 != value2:
                    return False

        return True

    def __get_header_content_info(self, section):
        """安全获取文档的页眉信息（包括检查页眉是否存在）"""
        result = {}
        try:
            def safe_extract_header(header_type, name):
                """安全提取页眉信息（如果页眉不存在则返回空数据）"""
                try:
                    header = section.Headers(header_type)
                    if not header.Exists:  # 关键检查：页眉是否实际存在
                        return None
                    range_ = header.Range
                    border = range_.Borders(win32.constants.wdBorderBottom)

                    return {
                        "paragraph": range_.Text.strip(),
                        "format":
                        {
                        "name": range_.Font.Name,
                        "size": range_.Font.Size,
                        "alignment": range_.ParagraphFormat.Alignment,
                         },
                        "border_line": border.LineStyle if border.LineStyle != 0 else None  # 0=无边框
                    }
                except Exception as e:
                    print(f"读取页眉 {name} 失败: {str(e)}")
                    return None

            # 主页眉（始终尝试读取）
            if primary_info := safe_extract_header(win32.constants.wdHeaderFooterPrimary, "primary"):
                result['primary'] = primary_info

            footer_header = self.__get_footer_header_info(section)
            # 首页页眉（仅在启用时读取）
            if footer_header['different_first_page']:
                if first_info := safe_extract_header(win32.constants.wdHeaderFooterFirstPage, "first"):
                    result['first'] = first_info

            # 偶数页页眉（仅在启用时读取）
            if footer_header['different_odd_even']:
                if even_info := safe_extract_header(win32.constants.wdHeaderFooterEvenPages, "even"):
                    result['even'] = even_info

        except Exception as e:
            print(f"获取页眉信息时发生错误: {str(e)}")

        return result

    def __get_footer_content_info(self, section):
        """安全获取文档的页脚信息（包括页码设置）"""
        result = {}
        try:
            def safe_extract_footer(footer_type, name):
                """安全提取页脚信息（如果页脚不存在或未设置页码则返回None）"""
                try:
                    footer = section.Footers(footer_type)
                    if not footer.Exists:  # 关键检查：页脚是否存在
                        return None
                    footer_range = footer.Range
                    if footer_range.Fields.Count == 0:  # 检查是否有页码字段
                        return None
                    page_numbers = footer.PageNumbers
                    return {
                        "format": page_numbers.NumberStyle,
                        "start": page_numbers.StartingNumber,
                        "continue": not page_numbers.RestartNumberingAtSection,
                        "alignment": footer_range.ParagraphFormat.Alignment,
                        "name": footer_range.Font.Name,
                        "size": footer_range.Font.Size
                    }
                except Exception as e:
                    print(f"读取页脚 {name} 失败: {str(e)}")
                    return None

            # 主页脚（始终尝试读取）
            if primary_info := safe_extract_footer(win32.constants.wdHeaderFooterPrimary, "primary"):
                result['primary'] = primary_info

            # 首页页脚（仅在启用时读取）

            footer_header = self.__get_footer_header_info(section)
            if footer_header['different_first_page']:
                if first_info := safe_extract_footer(win32.constants.wdHeaderFooterFirstPage, "first"):
                    result['first'] = first_info

            # 偶数页页脚（仅在启用时读取）
            if footer_header['different_odd_even']:
                if even_info := safe_extract_footer(win32.constants.wdHeaderFooterEvenPages, "even"):
                    result['even'] = even_info

        except Exception as e:
            print(f"获取页脚信息时发生错误: {str(e)}")

        return result

    def __get_footer_header_info(self, section):
        page_setup = section.PageSetup
        return {
            'different_first_page': (page_setup.DifferentFirstPageHeaderFooter == -1), # -1=True, 0=False
            'different_odd_even': (page_setup.OddAndEvenPagesHeaderFooter == -1),
            'footer_distance': page_setup.FooterDistance,
            'header_distance': page_setup.HeaderDistance
        }

    def __get_margin_info(self,section):
        page_setup = section.PageSetup
        return {
            "top": page_setup.TopMargin,  # 上边距
            "bottom": page_setup.BottomMargin,  # 下边距
            "left": page_setup.LeftMargin,  # 左边距
            "right": page_setup.RightMargin,  # 右边距
        }

    def __get_paper_info(self,section):
        page_setup = section.PageSetup
        return {
            "size": page_setup.PaperSize,  # 纸张大小
            "width": page_setup.PageWidth,  # 页面宽度
            "height": page_setup.PageHeight,  # 页面高度
            "orientation": page_setup.Orientation,  # 页面方向（0: 纵向, 1: 横向）
        }

    def __get_grid_info(self,section):
        page_setup = section.PageSetup
        return {
                "layout_mode": page_setup.LayoutMode,  # 布局模式
                "lines_page": page_setup.LinesPage,  # 每页行数
                "chars_line": page_setup.CharsLine,  # 每页字符数
            }

    def __get_column_info(self,section):
        # 获取该节的 TextColumns 属性
        page_setup = section.PageSetup
        text_columns = page_setup.TextColumns
        return {
                "column_count": text_columns.Count,  # 栏数
                "spacing": text_columns.Spacing,  # 栏距
                "evenly_spaced": text_columns.EvenlySpaced,  # 是否均匀分布
                "line_between": text_columns.LineBetween,  # 是否显示栏线
                "first_column_width": text_columns(1).Width  # 第一栏栏宽
            }

    def __get_gutter_info(self,section):
        page_setup = section.PageSetup
        return {
            "gutter": page_setup.Gutter,  # 装订线宽度
            "gutter_pos": page_setup.GutterPos,  # 装订线位置（0: 靠左, 1: 靠上）
        }

    def __get_section_x_pages(self, section, start_page, end_page, x=1):
        """
        获取指定节的前 x 页文本内容（段落粒度，不严格切分页）
        :param doc: Word 文档对象
        :param section_index: 节索引（从 1 开始）
        :param start_page: 该节的起始物理页码
        :param end_page: 该节的结束物理页码
        :param x: 前 x 页
        :return: str
        """
        rng = section.Range
        texts = []
        # 计算目标页码范围
        target_page = min(start_page + x - 1, end_page)
        for para in rng.Paragraphs:
            prng = para.Range
            page = prng.Information(win32.constants.wdActiveEndPageNumber)
            if start_page <= page <= target_page:
                texts.append(prng.Text)
            elif page > target_page:
                break  # 超过目标页就结束
        return "".join(texts)

    def __get_section_properties(self, doc, section_index, params_list=[]):
        """
                读取Word文档的页面属性
                :param doc: Word文档对象
                 section_index: 节的索引
                 params_list 获取该节的属性列表，如果不指定会返回所有信息而不是空
                :return: 操作结果（包含状态和页面属性信息）
                """
        properties = {}
        attribution_dict = {
            "margin": self.__get_margin_info,
            "gutter": self.__get_gutter_info,
            "paper": self.__get_paper_info,
            "grid": self.__get_grid_info,
            "columns": self.__get_column_info,
            "header_footer_layout": self.__get_footer_header_info,
            "footer_content": self.__get_footer_content_info,
            "header_content": self.__get_header_content_info
        }
        if not params_list:
            params_list = attribution_dict.keys()
        try:
            # 获取页面节对象
            section = doc.Sections(section_index)
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    attribution_info_get_tool = attribution_dict.get(params)
                    attribution_info = attribution_info_get_tool(section)
                    properties[params] = attribution_info
            # 返回成功结果
            return {"state": "success", "properties": properties}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "exception": str(e)}

    def __get_section_info(self, doc, section_index, content_x=1):
        """
        获取文档的一个section中的格式属性以及前x页的文本内容
        :param doc: Word文档对象
         section_index: 节的索引
         content_x = 1 获取几页的内容
        :return: 操作结果（包含状态和页面属性信息）
        """
        try:
            # 获取页面设置对象
            section = doc.Sections(section_index)
            rng = section.Range
            # 起始页码
            start_rng = rng.Duplicate
            start_rng.Collapse(win32.constants.wdCollapseStart)
            start_page = start_rng.Information(win32.constants.wdActiveEndPageNumber)

            # 结束页码
            end_rng = rng.Duplicate
            end_rng.Collapse(win32.constants.wdCollapseEnd)
            end_page = end_rng.Information(win32.constants.wdActiveEndPageNumber)

            content = self.__get_section_x_pages(section, start_page, end_page, x=content_x)
            # 返回成功结果
            return {"state": "success", "section_index": section_index,
                    "section_range": {"start": start_page, "end": end_page-1},
                    # "page_format": properties,
                    "content": content}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "exception": str(e)}


    def __read_header_content_info(self, section,page_info,pop_None, *args,**kwargs):
        # 页眉
        header = self.__get_header_content_info(section)
        for key in ["primary", "first", "even"]:
            if key in header:
                # print(key)
                page_info["header_content"][key]['text']["value"] = header.get(key).get("paragraph")
                page_info["header_content"][key]['name']["value"] = header.get(key).get("format").get('name')
                page_info["header_content"][key]['size']["value"] = header.get(key).get("format").get('size')
                page_info["header_content"][key]['alignment']["value"] = header.get(key).get("format").get('alignment')
                page_info["header_content"][key]['border_line']["value"] = header.get(key).get("border_line")
            else:
                if pop_None:
                    page_info["header_content"].pop(key)

        return page_info


    def __read_footer_content_info(self, section,page_info, pop_None, *args, **kwargs):
        # footer
        footer = self.__get_footer_content_info(section)
        # print(footer)
        for key in ["primary", "first", "even"]:
            if key in footer:
                page_info["footer_content"][key]['page_number']['format']["value"] = footer.get(key).get(
                    'format')
                page_info["footer_content"][key]['page_number']['start']["value"] = footer.get(key).get('start')
                page_info["footer_content"][key]['page_number']['continue']["value"] = footer.get(key).get(
                    'continue')
                page_info["footer_content"][key]['page_number']['alignment']["value"] = footer.get(key).get(
                    'alignment')
                page_info["footer_content"][key]['page_number']['name']["value"] = footer.get(key).get('name')
                page_info["footer_content"][key]['page_number']['size']["value"] = footer.get(key).get('size')
            else:
                if pop_None:
                    page_info["footer_content"].pop(key)

        return page_info


    def __read_footer_header_info(self, section, page_info, *args, **kwargs):
        # 页眉
        footer_header_info = self.__get_footer_header_info(section)
        header_dis = footer_header_info.get("header_distance")
        # print(header)
        page_info["header_footer_layout"]["header_distance"]["value"]["pt"] = header_dis
        page_info["header_footer_layout"]["header_distance"]["value"]["cm"] = self.pt_to_convert(header_dis, "cm")
        page_info["header_footer_layout"]["header_distance"]["value"]["mm"] = self.pt_to_convert(header_dis, "mm")
        page_info["header_footer_layout"]["header_distance"]["value"]["inches"] = self.pt_to_convert(header_dis, "inches")
        page_info["header_footer_layout"]["different_first_page"]["value"] = footer_header_info.get("different_first_page")
        page_info["header_footer_layout"]["different_odd_even"]["value"] = footer_header_info.get("different_odd_even")
        footer_dis = footer_header_info.get("footer_distance")
        page_info["header_footer_layout"]["footer_distance"]["value"]["pt"] = footer_dis
        page_info["header_footer_layout"]["footer_distance"]["value"]["cm"] = self.pt_to_convert(footer_dis, "cm")
        page_info["header_footer_layout"]["footer_distance"]["value"]["mm"] = self.pt_to_convert(footer_dis, "mm")
        page_info["header_footer_layout"]["footer_distance"]["value"]["inches"] = self.pt_to_convert(footer_dis, "inches")
        return page_info


    def __read_margin_info(self, section, page_info, *args, **kwargs):
        # 读取页面属性
        page_setup = section.PageSetup
        margin = {
                "top": page_setup.TopMargin,  # 上边距
                "bottom": page_setup.BottomMargin,  # 下边距
                "left": page_setup.LeftMargin,  # 左边距
                "right": page_setup.RightMargin,  # 右边距
            }
        # 页面边距属性填入
        for key, value in margin.items():
            page_info["margin"][key]["value"]["pt"] = value
            page_info["margin"][key]["value"]["cm"] = self.pt_to_convert(value, "cm")
            page_info["margin"][key]["value"]["mm"] = self.pt_to_convert(value, "mm")
            page_info["margin"][key]["value"]["inches"] = self.pt_to_convert(value, "inches")
        return page_info


    def __read_paper_info(self, section, page_info, *args, **kwargs):
        page_setup = section.PageSetup
        # 纸张值填入
        page_info["paper"]["size"]["value"] = page_setup.PaperSize  # 纸张大小
        page_info["paper"]["orientation"]["value"] = page_setup.Orientation  # 页面方向（0: 纵向, 1: 横向）
        # 纸张大小
        paper = {
            "width": page_setup.PageWidth,  # 页面宽度
            "height": page_setup.PageHeight,  # 页面高度
        }
        for key, value in paper.items():
            page_info["paper"][key]["value"]["pt"] = value
            page_info["paper"][key]["value"]["cm"] = self.pt_to_convert(value, "cm")
            page_info["paper"][key]["value"]["mm"] = self.pt_to_convert(value, "mm")
            page_info["paper"][key]["value"]["inches"] = self.pt_to_convert(value, "inches")
        return page_info


    def __read_grid_info(self, section, page_info, *args,**kwargs):
        page_setup = section.PageSetup
        # 布局值填入
        page_info["grid"]["layout_mode"]["value"] = page_setup.LayoutMode  # 布局模式
        page_info["grid"]["lines_page"]["value"] = page_setup.LinesPage  # 每页行数
        page_info["grid"]["chars_line"]["value"] = page_setup.CharsLine  # 每页字符数
        return page_info

    def __read_column_info(self,section,page_info,*args,**kwargs):
        text_columns = section.PageSetup.TextColumns
        # columns 分栏信息
        page_info["columns"]["column_count"]["value"] = text_columns.Count  # 栏数
        page_info["columns"]["evenly_spaced"]["value"] = text_columns.EvenlySpaced  # 是否均匀分布
        page_info["columns"]["line_between"]["value"] = text_columns.LineBetween  # 是否显示栏线
        columns = {
            "spacing": text_columns.Spacing,  # 栏距
            "column_width": text_columns(1).Width  # 栏宽
        }
        for key, value in columns.items():
            page_info["columns"][key]["value"]["pt"] = value
            page_info["columns"][key]["value"]["cm"] = self.pt_to_convert(value, "cm")
            page_info["columns"][key]["value"]["mm"] = self.pt_to_convert(value, "mm")
            page_info["columns"][key]["value"]["inches"] = self.pt_to_convert(value, "inches")

        return page_info

    def __read_gutter_info(self, section, page_info,*args,**kwargs):
        page_setup = section.PageSetup
        page_info["gutter"]["gutter"]["value"]['pt'] = page_setup.Gutter  # 装订线宽度
        page_info["gutter"]["gutter"]["value"]['cm'] = self.pt_to_convert(page_setup.Gutter ,"cm")# 装订线宽度
        page_info["gutter"]["gutter"]["value"]['mm'] = self.pt_to_convert(page_setup.Gutter,"mm")# 装订线宽度
        page_info["gutter"]["gutter"]["value"]['inches'] =self.pt_to_convert(page_setup.Gutter ,"inches")# 装订线宽度
        page_info["gutter"]["gutter_pos"]["value"] = page_setup.GutterPos  # 装订线宽度
        return page_info


    def get_page_properties(self, doc, section_index, params_list=[]):
        """
                读取Word文档的页面属性
                :param doc: Word文档对象
                 section_index: 节的索引
                 params_list 获取该节的属性列表，如果不指定会返回所有信息而不是空
                :return: 操作结果（包含状态和页面属性信息）
                """
        properties = {}
        attribution_dict = {
            "margin": self.__get_margin_info,
            "gutter": self.__get_gutter_info,
            "paper": self.__get_paper_info,
            "grid": self.__get_grid_info,
            "columns": self.__get_column_info,
            "header_footer_layout": self.__get_footer_header_info,
            "footer_content": self.__get_footer_content_info,
            "header_content": self.__get_header_content_info
        }
        if not params_list:
            params_list = attribution_dict.keys()
        try:
            # 获取页面节对象
            section = doc.Sections(section_index)
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    attribution_info_get_tool = attribution_dict.get(params)
                    attribution_info = attribution_info_get_tool(section)
                    properties[params] = attribution_info
            # 返回成功结果
            return {"state": "success", "properties": properties}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "exception": str(e)}

    def get_section_properties(self, doc, section_index, params_list=[], content_x=1):
        """
        获取文档的一个section中的格式属性以及前x页的文本内容
        :param doc: Word文档对象
         section_index: 节的索引
         content_x = 1 获取几页的内容
        :return: 操作结果（包含状态和页面属性信息）
        """
        try:
            # 获取页面设置对象
            section = doc.Sections(section_index)
            properties = self.get_page_properties(doc,section_index,params_list)

            rng = section.Range
            # 起始页码
            start_rng = rng.Duplicate
            start_rng.Collapse(win32.constants.wdCollapseStart)
            start_page = start_rng.Information(win32.constants.wdActiveEndPageNumber)

            # 结束页码
            end_rng = rng.Duplicate
            end_rng.Collapse(win32.constants.wdCollapseEnd)
            end_page = end_rng.Information(win32.constants.wdActiveEndPageNumber)

            content = self.__get_section_x_pages(section, start_page, end_page, x=content_x)
            # 返回成功结果
            return {"state": "success", "section_index": section_index,
                    "section_range": {"start": start_page, "end": end_page-1}, "page_format": properties,
                    "content": content}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "exception": str(e)}

    def read_page_properties(self,doc, section_index, params_list=[], language = 'zh', pop_None = True, *args,**kwargs):
        try:
            section_index = int(section_index)
            # 获取页面设置对象
            section = doc.Sections(section_index)
            attribution_dict = {
                "margin": self.__read_margin_info,
                "gutter": self.__read_gutter_info,
                "paper": self.__read_paper_info,
                "grid": self.__read_grid_info,
                "columns": self.__read_column_info,
                "header_footer_layout": self.__read_footer_header_info,
                "footer_content": self.__read_footer_content_info,
                "header_content": self.__read_header_content_info
            }
            # 加载读取模板
            template = self.config.get("properties_template")
            if language in['zh','en']:
                page_info = copy.deepcopy(template.get(language))
            else:
                page_info = copy.deepcopy(template.get("zh"))
                print("Default Using Chinese")

            if not params_list:
                # 未指定读取属性范围，默认全都要读取
                params_list = list(attribution_dict.keys())
            else:
                # 指定读取属性范围以后，删除模板中不需要读取的键值对
                for attribution in attribution_dict.keys():
                    if attribution not in params_list:
                        page_info.pop(attribution)

            # print(page_info)
            # 依次获取要读取的属性
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    # print("=" * 50)
                    # print(params)
                    attribution_info_read_tool = attribution_dict.get(params)
                    page_info = attribution_info_read_tool(section,page_info,pop_None)
                    # print(page_info)

            # 返回成功结果
            return {"state": "success", "properties":  page_info}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false","section_index":section_index, "exception": str(e)}


if __name__ == '__main__':
    from constant import ABS_DIR
    import os
    word = win32.DispatchEx("Word.Application")
    word.Visible = True  # 设为可见（调试时建议开启）
    # word_file_path = "file/base_1.docx"
    word_file_path = "file/template.docx"
    word_file_path = os.path.join(ABS_DIR,word_file_path)
    # 打开已有文档
    try:
        # 打开文档
        doc = word.Documents.Open(word_file_path)
        reader_tool = PageReader()

        print(reader_tool.get_page_styles(doc))

    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        # 确保清理资源
        if 'doc' in locals():
            doc.Close(SaveChanges=False)
        word.Quit()


