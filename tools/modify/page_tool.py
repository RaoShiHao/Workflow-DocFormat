import os
from win32com.client import constants
import win32com.client as win32
from tools.modify.tool_config import ContextToolsConfig

class BasePageTools():
    def convert_to_pt(self, value, unit):
        execl = win32.Dispatch("Excel.Application")
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit == "pt" or unit == "point" or unit == "磅":
            return float(value)
        elif unit == "cm" or unit == "centimeter" or unit == "厘米":
            return execl.CentimetersToPoints(value)
        elif unit == "mm" or unit == "millimeter" or unit == "毫米":
            return execl.CentimetersToPoints(value * 0.1)
        elif unit == "inches" or unit == "英寸":
            return execl.InchesToPoints(value)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def set_footer_header_layout(self, doc, section_index, different_first_page=0, different_odd_even=0,
                                 header_distance=None, footer_distance=None, *args, **kwargs):
        """
        设置指定节（或全局）的页眉页脚布局，包括不同首页、奇偶页开关，以及页眉页脚距边距离

        :param doc: Word 文档对象
        :param section_index: 节索引（整数 或 'all'）
        :param different_first_page: 首页不同页眉页脚 (0/-1)
        :param different_odd_even: 奇偶页不同页眉页脚 (0/-1)
        :param header_distance: dict，页眉距顶部距离
        :param footer_distance: dict，页脚距底部距离
        :return: dict 结果
        """
        results = {}
        try:
            if section_index == "all":
                page_setup = doc.PageSetup
            else:
                page_setup = doc.Sections(section_index).PageSetup
            # 首页不同
            try:
                page_setup.DifferentFirstPageHeaderFooter = different_first_page
                results["different_first_page"] = {"status": "success",
                                                   "message": f"Different first page header footer set to {different_first_page}"}
            except Exception as e:
                results["different_first_page"] = {
                    "status": "error",
                    "message": f"Failed to set different first page header footer, the detailed is: {str(e)}"
                }

            # 奇偶页不同
            try:
                page_setup.OddAndEvenPagesHeaderFooter = different_odd_even
                results["different_odd_even"] = {
                    "status": "success",
                    "message": f"Different odd/even pages footer set to {different_odd_even}"
                }
            except Exception as e:
                results["different_odd_even"] = {
                    "status": "error",
                    "message": f"Failed to set different odd/even pages footer, the detailed is: {str(e)}"
                }

            # 页眉距边
            if header_distance:
                try:
                    page_setup.HeaderDistance = header_distance
                    results["header_distance"] = {
                        "status": "success",
                        "message": f"Header distance set to {header_distance} pt."
                    }
                except Exception as e:
                    results["header_distance"] = {
                        "status": "error",
                        "message": f"Failed to set header distance, the detailed is: {str(e)}"
                    }

            # 页脚距边
            if footer_distance:
                try:
                    page_setup.FooterDistance = footer_distance
                    results["footer_distance"] = {
                        "status": "success",
                        "message": f"Footer distance set to {footer_distance} pt."
                    }
                except Exception as e:
                    results["footer_distance"] = {
                        "status": "error",
                        "message": f"Failed to set footer distance, the detailed is: {str(e)}"
                    }

            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
            }
        return results

    def set_header_content(self, doc, section_index, first=None, primary=None, even=None,
                           different_first_page=0, different_odd_even=0, *args, **kwargs):
        """
        设置指定节的页眉内容（文字、格式、边框）
        :param doc: Word 文档对象
        :param section_index: 节索引，整数 或 'all'
        :param first: dict 首页页眉配置
        :param primary: dict 常规/奇数页页眉配置
        :param even: dict 偶数页页眉配置
        :param different_first_page: 是否启用首页不同页眉
        :param different_odd_even: 是否启用奇偶页不同页眉
        """
        results = {}

        try:
            # 统一确定要处理的节
            if section_index == "all":
                sections = list(doc.Sections)
            else:
                sections = [doc.Sections(section_index)]
            for idx, section in enumerate(sections, start=1):
                header_map = {
                    "first": (win32.constants.wdHeaderFooterFirstPage, first, different_first_page == -1),
                    "primary": (win32.constants.wdHeaderFooterPrimary, primary, True),
                    "even": (win32.constants.wdHeaderFooterEvenPages, even, different_odd_even == -1),
                }
                for name, (header_type, config, enabled) in header_map.items():
                    if config and enabled:
                        try:
                            rng = section.Headers(header_type).Range
                            rng.Text = config.get("paragraph", "")

                            fmt = config.get("format", {})
                            if "alignment" in fmt:
                                rng.ParagraphFormat.Alignment = fmt["alignment"]
                            if "name" in fmt:
                                rng.Font.Name = fmt["name"]
                            if "size" in fmt:
                                rng.Font.Size = fmt["size"]
                            if "border_line" in config:
                                rng.Borders(win32.constants.wdBorderBottom).LineStyle = config["border_line"]

                            results[name] = {
                                "status": "success",
                                "message": "Header content applied successfully"
                            }
                        except Exception as e:
                            results[name] = {
                                "status": "error",
                                "message": f"Failed to apply header content, the detailed is: {str(e)}"
                            }

            doc.Save()

        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
            }
        return results

    def set_footer_content(self, doc, section_index, first=None, primary=None, even=None,
                           different_first_page=0, different_odd_even=0, *args, **kwargs):
        """
        抗模板污染版本：
        - 断开 LinkToPrevious
        - 正确设置 Section 级别开关
        - 不混用 PageNumbers / Field 体系
        - 强制清理历史页码状态
        """

        results = {}

        try:
            # 1. 确定节
            if section_index == "all":
                sections = list(doc.Sections)
            else:
                sections = [doc.Sections(section_index)]

            for sec_idx, section in enumerate(sections, start=1):

                # 2. Section 级别开关（必须先设）
                section.PageSetup.DifferentFirstPageHeaderFooter = (different_first_page == -1)
                section.PageSetup.OddAndEvenPagesHeaderFooter = (different_odd_even == -1)

                def apply_footer(footer_type, config):
                    if not config:
                        return

                    cfg = config
                    # 3. 获取页脚并断开继承（核心）
                    footer = section.Footers(footer_type)
                    footer.LinkToPrevious = False

                    rng = footer.Range

                    # 4. 彻底清空内容（比 Delete 稳定）
                    rng.Text = ""

                    # 5. 清理潜在残留 Field（防模板污染）
                    try:
                        while rng.Fields.Count > 0:
                            rng.Fields(1).Delete()
                    except Exception:
                        pass

                    # 6. 配置 PageNumbers（只负责“逻辑”，不插入）
                    page_numbers = footer.PageNumbers
                    page_numbers.RestartNumberingAtSection = not cfg.get("continue", True)
                    page_numbers.StartingNumber = cfg.get("start", 1)
                    page_numbers.NumberStyle = cfg.get("format", 0)

                    # 7. 插入 PAGE 域（只用一种体系）
                    field = rng.Fields.Add(
                        Range=rng,
                        Type=win32.constants.wdFieldPage
                    )

                    # 8. 段落格式
                    rng.ParagraphFormat.Alignment = cfg.get("alignment", 1)

                    # 9. 字体格式
                    rng.Font.Name = cfg.get("name", "Times New Roman")
                    rng.Font.Size = cfg.get("size", 12)

                footer_map = {
                    "first": (
                        win32.constants.wdHeaderFooterFirstPage,
                        first,
                        different_first_page == -1
                    ),
                    "primary": (
                        win32.constants.wdHeaderFooterPrimary,
                        primary,
                        True
                    ),
                    "even": (
                        win32.constants.wdHeaderFooterEvenPages,
                        even,
                        different_odd_even == -1
                    ),
                }

                for name, (footer_type, cfg, enabled) in footer_map.items():
                    if cfg and enabled:
                        try:
                            apply_footer(footer_type, cfg)
                            results[f"section_{sec_idx}_{name}"] = {
                                "status": "success"
                            }
                        except Exception as e:
                            results[f"section_{sec_idx}_{name}"] = {
                                "status": "error",
                                "message": str(e)
                            }

            # 10. 全文强制刷新
            doc.Fields.Update()
            doc.Save()

        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": str(e)
            }

        return results

    def set_margin(self, doc, section_index, top, bottom, left, right, *args, **kwargs):
        """
        Set page margin properties for a Word document

        :param doc: Word document object
        :param top: Top margin (dict with value and unit)
        :param bottom: Bottom margin (dict with value and unit)
        :param left: Left margin (dict with value and unit)
        :param right: Right margin (dict with value and unit)
        :return: Dictionary containing results for each margin setting
        """
        results = {}

        try:
            if section_index == 'all':
                page_setup = doc.PageSetup
            else:
                page_setup = doc.Sections(section_index).PageSetup
            # Set top margin
            if top:
                try:
                    page_setup.TopMargin = top
                    results["top_margin"] = {
                        "status": "success",
                        "message": f"Top margin set to {top} pt."
                    }
                except Exception as e:
                    results["top_margin"] = {
                        "status": "error",
                        "message": f"Failed to set top margin, the detailed is: {str(e)}"
                    }

            # Set bottom margin
            if bottom:
                try:
                    page_setup.BottomMargin = bottom
                    results["bottom_margin"] = {
                        "status": "success",
                        "message": f"Bottom margin set to {bottom} pt."
                    }
                except Exception as e:
                    results["bottom_margin"] = {
                        "status": "error",
                        "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
                    }

            # Set left margin
            if left:
                try:
                    page_setup.LeftMargin = left
                    results["left_margin"] = {
                        "status": "success",
                        "message": f"Left margin set to {left}."
                    }
                except Exception as e:
                    results["left_margin"] = {
                        "status": "error",
                        "message": f"Failed to set left margin, the detailed is: {str(e)}"
                    }

            # Set right margin
            if right:
                try:
                    page_setup.RightMargin = right
                    results["right_margin"] = {
                        "status": "success",
                        "message": f"Right margin set to {right} pt."
                    }
                except Exception as e:
                    results["right_margin"] = {
                        "status": "error",
                        "message": f"Failed to set right margin, the detailed is: {str(e)}"
                    }

            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
            }
        return results

    def set_gutter(self, doc, section_index, gutter=0, gutter_unit='pt', gutter_pos=0, *args, **kwargs):
        """
        Set gutter properties for a Word document

        :param doc: Word document object
        :param gutter: Gutter width in points
        :param gutter_pos: Gutter position (0: Left, 1: Top)
        :return: Dictionary containing results for each gutter setting
        """
        results = {}
        try:
            if section_index == 'all':
                page_setup = doc.PageSetup
            else:
                page_setup = doc.Sections(section_index).PageSetup
            # Set gutter width
            try:
                gutter_value = gutter
                gutter = self.convert_to_pt(gutter_value, gutter_unit)
                page_setup.Gutter = gutter
                # print("gutter_value:",gutter_value)
                # print("gutter:",gutter)
                results["gutter_width"] = {
                    "status": "success",
                    "message": f"Gutter width set to {gutter} points(from {gutter_value} {gutter_unit}) "
                }
            except Exception as e:
                results["gutter_width"] = {
                    "status": "error",
                    "message": f"Failed to set gutter width, the detailed is: {str(e)}"
                }

            # Set gutter position
            try:
                page_setup.GutterPos = gutter_pos
                position_text = "Left" if gutter_pos == 0 else "Top"
                results["gutter_position"] = {
                    "status": "success",
                    "message": f"Gutter position set to {position_text}"
                }
            except Exception as e:
                results["gutter_position"] = {
                    "status": "error",
                    "message": f"Failed to set gutter position, the detailed is: {str(e)}"
                }
            doc.Save()
        except Exception as e:
            results["error"] = {"status": "error",
                                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"}

        doc.Save()
        return results

    def set_paper(self, doc, section_index, size=None, width=None, height=None, orientation=None, *args, **kwargs):
        """
        Set paper properties for a Word document

        :param doc: Word document object
        :param size: Paper size constant
        :param width: page width (dict with value and unit)
        :param height: page height (dict with value and unit)
        :param orientation: page orientation (0: Portrait, 1: Landscape)
        :return: Dictionary containing results for each parameter setting
        """
        results = {}
        try:
            if section_index == 'all':
                page_setup = doc.PageSetup
            else:
                page_setup = doc.Sections(section_index).PageSetup

            # Set orientation
            if orientation is not None:
                try:
                    page_setup.Orientation = orientation
                    orientation_text = "Portrait" if orientation == 0 else "Landscape"
                    results["orientation"] = {
                        "status": "success",
                        "message": f"Orientation set to {orientation_text}"
                    }
                except Exception as e:
                    results["orientation"] = {
                        "status": "error",
                        "message": f"Failed to set orientation, the detailed is: {str(e)}"
                    }
            # Set paper size
            if size is not None:
                try:
                    page_setup.PaperSize = size
                    results["paper_size"] = {
                        "status": "success",
                        "message": f"Paper size set to {size}"
                    }
                except Exception as e:
                    results["paper_size"] = {
                        "status": "error",
                        "message": f"Failed to set paper size, the detailed is: {str(e)}"
                    }

            # Set page width
            if width:
                try:
                    page_setup.PageWidth = width
                    results["page_width"] = {
                        "status": "success",
                        "message": f"page width set to {width} pt."
                    }
                except Exception as e:
                    results["page_width"] = {
                        "status": "error",
                        "message": f"Failed to set page width, the detailed is: {str(e)}"
                    }

            # Set page height
            if height:
                try:
                    page_setup.PageHeight = height
                    results["page_height"] = {
                        "status": "success",
                        "message": f"page height set to {height} pt."
                    }
                except Exception as e:
                    results["page_height"] = {
                        "status": "error",
                        "message": f"Failed to set page height, the detailed is: {str(e)}"
                    }



            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
            }
        return results

    def set_grid(self, doc, section_index, layout_mode=None, lines_page=None, chars_line=None, *args, **kwargs):
        """
        Set document grid properties for a Word document

        :param doc: Word document object
        :param layout_mode: Layout mode setting
        :param lines_page: Lines per page
        :param chars_line: Characters per line
        :return: Dictionary containing results for each parameter setting
        """
        results = {}
        if section_index == 'all':
            page_setup = doc.PageSetup
        else:
            page_setup = doc.Sections(section_index).PageSetup

        # Process layout_mode
        if layout_mode is not None:
            try:
                page_setup.LayoutMode = layout_mode
                results["layout_mode"] = {
                    "status": "success",
                    "message": f"1 Layout mode set to {page_setup.LayoutMode}"
                }
            except Exception as e:
                results["layout_mode"] = {
                    "status": "error",
                    "message": f"Failed to set layout mode, the detailed is : {str(e)}"
                }

        # Process chars_line
        if chars_line is not None and layout_mode > 0:
            try:
                page_setup.CharsLine = chars_line
                results["chars_line"] = {
                    "status": "success",
                    "message": f"Characters per line set to {chars_line}"
                }
            except Exception as e:
                results["chars_line"] = {
                    "status": "error",
                    "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
                }

        # Process lines_page
        if lines_page is not None and layout_mode > 0:
            try:
                page_setup.LinesPage = lines_page
                results["lines_page"] = {
                    "status": "success",
                    "message": f"Lines per page set to {lines_page}"
                }
            except Exception as e:
                results["lines_page"] = {
                    "status": "error",
                    "message": f"Failed to set lines per page, the detailed is : {str(e)}"
                }

        doc.Save()
        return results

    def set_columns(self, doc, section_index, column_count=1, evenly_spaced=0, column_width=None, spacing=None,
                    line_between=0, *args, **kwargs):
        """
        Set column layout for sections in a Word document

        :param doc: Word document object
        :param column_count: Number of columns
        :param evenly_spaced: Whether columns are evenly spaced (1=yes, 0=no)
        :param column_width: Column width settings (dict: value + unit)
        :param spacing: Column spacing settings (dict: value + unit)
        :param line_between: Whether to show line between columns (1=show, 0=hide)
        :return: Dictionary containing results for each parameter setting
        """
        results = {}
        try:
            if section_index == 'all':
                text_columns = doc.PageSetup.TextColumns
            else:
                section = doc.Sections(section_index)
                text_columns = section.PageSetup.TextColumns

            # Set column count
            try:
                text_columns.SetCount(column_count)
                results["column_count"] = {
                    "status": "success",
                    "message": f"Column count set to {column_count}"
                }
            except Exception as e:
                results["column_count"] = {
                    "status": "error",
                    "message": f"Failed to set column count, the detailed is: {str(e)}"
                }

            # Set evenly spaced
            try:
                if evenly_spaced:
                    text_columns.EvenlySpaced = evenly_spaced
                    results["evenly_spaced"] = {
                        "status": "success",
                        "message": f"Evenly spaced set to {evenly_spaced}"
                    }
            except Exception as e:
                results["evenly_spaced"] = {
                    "status": "error",
                    "message": f"Failed to set evenly spaced, the detailed is: {str(e)}"
                }

            # Set line between
            try:
                if line_between:
                    text_columns.LineBetween = line_between
                    results["line_between"] = {
                        "status": "success",
                        "message": f"Line between columns set to {line_between}"
                    }
            except Exception as e:
                results["line_between"] = {
                    "status": "error",
                    "message": f"Failed to set line between, the detailed is: {str(e)}"
                }

            # Set column width
            if column_width:
                try:
                    text_columns.Item(1).Width = column_width
                    results["column_width"] = {
                        "status": "success",
                        "message": f"Column width set to {column_width} pt."
                    }
                except Exception as e:
                    results["column_width"] = {
                        "status": "error",
                        "message": f"Failed to set column width, the detailed is: {str(e)}"
                    }

            # Set spacing
            if spacing:
                try:
                    text_columns.Spacing = spacing
                    results["spacing"] = {
                        "status": "success",
                        "message": f"Column spacing set to {spacing} pt."
                    }
                except Exception as e:
                    results["spacing"] = {
                        "status": "error",
                        "message": f"Failed to set column spacing, the detailed is: {str(e)}"
                    }
            doc.Save()
        except Exception as e:
            results["error"] = {
                "status": "error",
                "message": f"Failed to access section {section_index} setup, the detailed is: {str(e)}"
            }

        return results

class PageTools():
    def __init__(self):
        self.page_tool = BasePageTools()

    def __set_footer_header_layout(self, doc, section_list, different_first_page=0, different_odd_even=0,
                                 header_distance=None, footer_distance=None, *args, **kwargs):
        result = None
        if different_first_page is True:
            different_first_page = -1
        elif different_first_page is False:
            different_first_page = 0
        if different_odd_even is True:
            different_odd_even = -1
        elif different_odd_even is False:
            different_odd_even = 0

        for section_index in section_list:
            result = self.page_tool.set_footer_header_layout(doc, section_index, different_first_page, different_odd_even,
                                                             header_distance,footer_distance)
        return result

    def __set_header_content(self,doc, section_list, first=None, primary=None, even=None,
                           different_first_page=0, different_odd_even=0, *args, **kwargs ):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_header_content(doc, section_index, first, primary, even,
                           different_first_page, different_odd_even)
        return result

    def __set_footer_content(self, doc, section_list, first=None, primary=None, even=None,
                           different_first_page=0, different_odd_even=0, *args, **kwargs):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_footer_content(doc, section_index, first, primary, even,
                                                       different_first_page, different_odd_even)
        return result

    def __set_margin(self, doc, section_list, top={}, bottom={}, left={}, right={}, *args, **kwargs):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_margin(doc,section_index,top,bottom,left,right)
        return result

    def __set_gutter(self,doc, section_list, gutter=0, gutter_unit='pt', gutter_pos=0, *args, **kwargs):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_gutter(doc, section_index, gutter, gutter_unit, gutter_pos)
        return result

    def __set_paper(self,doc, section_list, size=None, width={}, height={}, orientation=None, *args, **kwargs):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_paper(doc, section_index, size, width, height, orientation)
        return result

    def __set_grid(self,doc, section_list, layout_mode=None, lines_page=None, chars_line=None, *args, **kwargs):
        result = None
        if not layout_mode:
            layout_mode = 0
        if layout_mode == 1:
            chars_line = None
        for section_index in section_list:
            result = self.page_tool.set_grid(doc, section_index, layout_mode, lines_page, chars_line)
        return result

    def __set_columns(self, doc, section_list, column_count=1, evenly_spaced=0, column_width={}, spacing={},
                    line_between=0, *args, **kwargs ):
        result = None
        for section_index in section_list:
            result = self.page_tool.set_columns(doc,section_index,column_count,evenly_spaced,column_width,spacing,line_between)
        return result


    def set_format(self,doc, location_list, settings={}):
        support_functions = {
            "paper": self.__set_paper,
            "gutter": self.__set_gutter,
            "margin": self.__set_margin,
            "columns": self.__set_columns,
            "grid": self.__set_grid,
            "footer_content": self.__set_footer_content,
            "header_content": self.__set_header_content,
            "header_footer_layout": self.__set_footer_header_layout,
        }
        # 顺序：先paper,后gutter, margin，再columns，最后grid，header_footer_layout在最后
        support_properties = list(support_functions.keys())
        format_modify_results = {}
        for support_property in support_properties:
            property_setting = settings.get(support_property)
            if property_setting:
                property_function = support_functions.get(support_property)
                modify_result = property_function(doc,location_list,**property_setting)
                format_modify_results[support_property] = modify_result
                print(modify_result)
        return format_modify_results


if __name__ == '__main__':
    from constant import ABS_DIR
    settings={
        # "margin": {
        #     "top": 53.849998474121094,
        #     "bottom": 70.9000015258789,
        #     "left": 56.70000076293945,
        #     "right": 56.70000076293945
        # },
        # "paper": {
        #     "size": 7,
        #     "height": 841.9500122070312
        # },
        # "grid": {
        #     "layout_mode": 1,
        #     "lines_page": 45.0,
        #     "chars_line": 23.0
        # },
        # "columns": {
        #     "column_count": 2,
        #     "spacing": 22.0,
        #     "first_column_width": 229.9499969482422
        # },
        "header_footer_layout": {
                "different_first_page": True,
                "footer_distance": 54.150001525878906
            },
        "footer_content": {
                "primary": {
                    "format": 0,
                    "start": 0,
                    "continue": True,
                    "alignment": 1,
                    "name": "宋体",
                    "size": 10.0
                }
            }
    }
    page_tool = PageTools()
    try:
        # 连接Word
        word = win32.DispatchEx("Word.Application")  # 或使用Dispatch
        # print(word.ActivePrinter)
        word.ActivePrinter = "Microsoft Print to PDF"
        word.Visible = True  # 设为不可见（调试时建议开启）
        docx_path = "file/source_content.docx"
        doc = word.Documents.Open(os.path.join(ABS_DIR, docx_path))

        result = page_tool.set_format(doc=doc,location_list=[1],settings=settings)
        print(result)
    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        # 确保清理资源
        if 'doc' in locals():
            doc.Close(SaveChanges=False)
        word.Quit()