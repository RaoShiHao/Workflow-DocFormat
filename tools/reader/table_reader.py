from constant import ABS_DIR
import os,copy
from win32com.client import constants
import win32com.client as win32
from tools.modify.tool_config import ContextToolsConfig
class TableReader():
    def __init__(self, pyconfig=ContextToolsConfig(config_path="config/reader/table_reader_config.yaml")):
        self.config = pyconfig.config

    def pt_to_convert(self, value, unit):
        if value is None or value in [99999,9999999]:
            return 99999
        value = float(value)
        # 加速运算，直接读取换算值
        # execl = win32com.client.Dispatch("Excel.Application")
        # cm_unit = execl.CentimetersToPoints(1)
        # inches_unit = execl.InchesToPoints(1)
        cm_unit = 28.346456692913385
        inches_unit = 72.0
        """将不同单位的间距转换为磅（pt）"""
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

    def pt_to_percent(self,value, PageSetup):
        page_width = PageSetup.PageWidth - PageSetup.LeftMargin - PageSetup.RightMargin
        percent = round(value / page_width * 100,2)
        return percent

    def get_table(self, doc, table_index):
        try:
            # Check if document object is valid
            if doc is None:
                raise ValueError("Document object cannot be None")

            # Check if document object has Tables attribute
            if not hasattr(doc, 'Tables'):
                raise ValueError("The provided object is not a valid Word document object")

            # Validate table index
            if not isinstance(table_index, int) or table_index < 1:
                raise ValueError(f"Table index must be a positive integer, got: {table_index}")

            # Get tables collection from document
            tables = doc.Tables

            # Check if any tables exist
            if tables.Count == 0:
                raise Exception("No tables found in the document")

            # Check if table index is within range
            if table_index > tables.Count:
                raise ValueError(
                    f"Table index {table_index} is out of range. Document contains {tables.Count} table(s)")

            # Get specified table (Word table indexing starts from 1)
            table = tables(table_index)

            # Validate table object
            if table is None:
                raise Exception(f"Retrieved table object is None for index: {table_index}")

            return table

        except ValueError as ve:
            # Re-raise ValueError with original type
            raise ve
        except Exception as e:
            # Wrap other exceptions with clearer error message
            error_msg = f"Failed to get table (index:{table_index}): {str(e)}"
            raise Exception(error_msg) from e

    def __get_column_width(self, table, col_index: int=1):
        """
        获取表格中某一列（或全部列）的宽度信息（pt值 + 规则）

        :param table: Word 表格对象
        :param col_index: 列号（1 表示第一列，0 表示所有列平均宽度）
        :return: dict，包含 width（pt）与 rule（exactly / auto / mixed）
        """
        if table.Columns.Count == 0:
            raise ValueError("Table has no columns")
        if col_index < 0 or col_index > table.Columns.Count:
            raise ValueError(f"Invalid col_index: {col_index}, table has {table.Columns.Count} columns")
        # 宽度规则映射表
        width_type_map = {
            constants.wdPreferredWidthPoints: "exactly",
            constants.wdPreferredWidthAuto: "auto"
        }
        # ---- 获取全部列的平均情况 ----
        if col_index == 0:
            total_width = 0.0
            rule_set = set()
            for col in table.Columns:
                total_width += col.Width
                rule_set.add(col.PreferredWidthType)
            avg_width = total_width / table.Columns.Count if table.Columns.Count > 0 else 0
            if len(rule_set) == 1:
                rule = width_type_map.get(next(iter(rule_set)), "unknown")
            else:
                rule = "mixed"
            width_pt = avg_width
        # ---- 获取指定列 ----
        else:
            col = table.Columns(col_index)
            width_pt = col.Width
            rule = width_type_map.get(col.PreferredWidthType, "unknown")
        return {
            "width": width_pt,
            "rule": rule
        }


    def __get_row_height(self, table, row_index: int=1):
        """
        获取表格中某一行的高度信息（pt值 + 规则）

        :param table: Word 表格对象
        :param row_index: 行号（1 表示第一行，0 表示所有行平均高度）
        :return: dict，包含 height（pt 或 None）与 rule（exactly/at_least/auto/mixed）
        """
        if table.Rows.Count == 0:
            raise ValueError("Table has no rows")
        if row_index < 0 or row_index > table.Rows.Count:
            raise ValueError(f"Invalid row_index: {row_index}, table has {table.Rows.Count} rows")

        # 映射表
        rule_map = {
            constants.wdRowHeightExactly: "exactly",
            constants.wdRowHeightAtLeast: "at_least",
            constants.wdRowHeightAuto: "auto",
        }

        # ---- 处理全表平均情况 ----
        if row_index == 0:
            total_height = 0.0
            valid_count = 0
            rule_set = set()

            for row in table.Rows:
                rule_code = row.HeightRule
                rule_str = rule_map.get(rule_code, "unknown")
                rule_set.add(rule_str)

                # 自动高度或无效值（99999）不计入平均
                if rule_code == constants.wdRowHeightAuto or row.Height >= 99999:
                    continue

                total_height += row.Height
                valid_count += 1

            avg_height = total_height / valid_count if valid_count > 0 else None
            rule = "mixed" if len(rule_set) > 1 else next(iter(rule_set), "unknown")

            return {"height": avg_height, "rule": rule}

        # ---- 获取指定行 ----
        else:
            row = table.Rows(row_index)
            rule_code = row.HeightRule
            rule = rule_map.get(rule_code, "unknown")

            # 若为自动高度或99999，则高度无意义
            if rule_code == constants.wdRowHeightAuto or row.Height >= 99999:
                height_pt = None
            else:
                height_pt = row.Height

            return {"height": height_pt, "rule": rule}


    def __get_table_width(self, table):
        """
        获取表格的当前宽度及宽度类型（单位：pt 或 百分比）

        :param table: Word 表格对象
        :return: dict，包含 width、unit、rule 三个字段
        """
        # ---- 宽度值及类型 ----
        width_type = table.PreferredWidthType

        if width_type == constants.wdPreferredWidthPoints:
            unit = "pt"
        elif width_type == constants.wdPreferredWidthPercent:
            unit = "percent"
        else:
            unit = "auto"

        # ---- 列宽规则 ----
        # 这里 AutoFitBehavior 没有直接的读取接口，
        # 我们可以通过表格属性推测当前模式：
        if table.AllowAutoFit:
            # 如果允许自动调整，则可能是根据内容或窗口
            # 注意：Word COM API 并不能直接区分 "auto_content" vs "auto_window"
            # 我们仅能推测大致类型
            rule = "auto"
        else:
            rule = "fixed"

        return {
            "width": table.PreferredWidth,
            "rule": rule,
            "unit":unit
        }

    def __get_table_height(self, table):
        """
        获取整个表格的总高度（pt值）
        :param table: Word 表格对象
        :return: dict，包含：
            {
                "height": float | None,   # 表格总高度（pt），若全部为自动行则返回 None
                "rule": str               # overall rule: exactly / at_least / auto / mixed
            }
        """
        if table.Rows.Count == 0:
            raise ValueError("Table has no rows")

        total_height = 0.0
        valid_count = 0
        rule_set = set()

        # 遍历所有行，累加行高
        for i in range(1, table.Rows.Count + 1):
            row_info = self.__get_row_height(table, i)
            rule_set.add(row_info["rule"])

            # 累加有效高度
            if row_info["height"] is not None:
                total_height += row_info["height"]
                valid_count += 1

        # 判断整体规则
        if len(rule_set) == 1:
            overall_rule = next(iter(rule_set))
        else:
            overall_rule = "mixed"

        # 若所有行均为自动高度，则无法计算总高
        if valid_count == 0:
            total_height_pt = None
        else:
            total_height_pt = total_height
        return {
            "height": total_height_pt,
            "rule": overall_rule
        }

    def __get_text_wrapping(self,table):
        return {
            "text_wrapping":table.Rows.WrapAroundText
        }


    def __get_pagination(self, table):
        return {
            "allow_break_across_pages": table.Rows.AllowBreakAcrossPages,
            "repeat_header": table.Rows(1).HeadingFormat if table.Rows.Count > 0 else 0,
            "keep_with_next": table.Range.ParagraphFormat.KeepWithNext,
            "page_break_before": table.Range.ParagraphFormat.PageBreakBefore
        }


    def __get_table_alignment(self, table):
        # ==== 获取表格整体水平对齐方式 ====
        horizontal_map = {
            0: "left",  # wdAlignParagraphLeft
            1: "center",  # wdAlignParagraphCenter
            2: "right"  # wdAlignParagraphRight
        }
        horizontal_value = table.Rows.Alignment
        horizontal_align = horizontal_map.get(horizontal_value, "unknown")
        # ==== 获取表格的整体垂直对齐方式 ====
        vertical_map = {
            0: "top",  # wdCellAlignVerticalTop
            1: "center",  # wdCellAlignVerticalCenter
            3: "bottom"  # wdCellAlignVerticalBottom
        }
        align_set = set()
        for row in table.Rows:
            for cell in row.Cells:
                vertical_value = cell.VerticalAlignment
                vertical_align = vertical_map.get(vertical_value, "unknown")
                align_set.add(vertical_align)
        # 判断结果
        if len(align_set) == 1:
            final_vertical_align = align_set.pop()  # 只有一种对齐方式
        else:
            final_vertical_align = "mix"  # 存在多种对齐方式
        # print("表格整体垂直对齐方式:", final_vertical_align)

        return {
            "horizontal_align": horizontal_align,
            "vertical_align": final_vertical_align
        }


    def __get_left_indent(self,table):
        return {
            "left_indent": table.Rows.LeftIndent
        }


    def __get_cell_vertical_alignment(self,table,row_index=1, col_index=1):
        # ==== 获取指定单元格的垂直对齐方式 ====
        vertical_map = {
            0: "top",  # wdCellAlignVerticalTop
            1: "center",  # wdCellAlignVerticalCenter
            3: "bottom"  # wdCellAlignVerticalBottom
        }
        cell = table.Cell(row_index, col_index)
        vertical_value = cell.VerticalAlignment
        vertical_align = vertical_map.get(vertical_value, "unknown")
        return {
            "cell_vertical_align": vertical_align
        }


    def get_tables_format(self,doc):
        table_num = doc.Tables.Count
        formats = []
        for index in range(table_num):
            table_index = index+1
            table = self.get_table(doc,table_index)
            format = self.get_table_format(doc,table_index)
            if format.get("state") == "success":
                style_name = table.Cell(1,1).Range.Text
                style_name = style_name.replace("\r\x07","")
                format = format.get("properties")
                formats.append({"style_name":style_name,"table_list":[table_index],"format_properties":format})
        return formats

    def get_table_format(self,doc,table_index):
        attribution_dict = {
            "column_width": self.__get_column_width,
            "row_height": self.__get_row_height,
            "table_width": self.__get_table_width,
            "table_height": self.__get_table_height,
            "text_wrapping": self.__get_text_wrapping,
            "pagination": self.__get_pagination,
            "alignment": self.__get_table_alignment,
            "left_indent": self.__get_left_indent,
        }
        try:
            # 字符串转换
            table_index = int(table_index)
            if table_index > 0:
                table = self.get_table(doc, table_index)
            else:
                print("table index must >= 0!")
                raise
            table_info = {
            "column_width": None,
            "row_height": None,
            "table_width": None,
            "table_height": None,
            "text_wrapping": None,
            "pagination": None,
            "alignment": None,
            "left_indent": None,
        }
            # 依次获取要读取的属性
            for attribution in attribution_dict:
                    attribution_info_read_tool = attribution_dict.get(attribution)
                    table_info[attribution] = attribution_info_read_tool(table)

            # 返回成功结果
            return {"state": "success", "properties": table_info}
        except Exception as e:
            print(f"Get table format error! The detail is {e}")
            return {"state": "success", "properties": None, "exception":e}


    def get_table_infos(self, doc, before=0,after=0):
        try:
            table_info_result = []
            for table_index in range(1, doc.Tables.Count + 1):
                table_table_result = self.__get_table_info(doc,table_index=table_index,  before=before, after=after)
                if table_table_result.get("state") == "success":
                    table_table_result.pop("state")
                    table_info_result.append(table_table_result)
            return table_info_result
        except Exception as e:
            print(f"Get table info error! The detail is: {e}")
            raise

    def __get_table_info(self, doc, table_index, before=0, after=0):
        """
        获取指定表格的详细信息。
        返回结构（示例）：
        {
            "table_index": 1,
            "cells": [
                {"row": 1, "col": 1, "paragraph": "Name", "paragraph_indices": [3]},
                {"row": 1, "col": 2, "paragraph": "Age", "paragraph_indices": [4]},
                ...
            ],
            "before_texts": ["表标题"],   # 最多 before 段（从表格紧邻上方向上取非空段）
            "after_texts": ["表后段1", ...]  # 最多 after 段（从表格紧邻下方开始向下取非空段）
        }

        原则和改动要点：
        - before: 不再用简单的 Range 距离阈值判断，而是取“紧邻表格的上方非空段落”，若需要多个则继续向上取不为空的段落。
        - after: 从表格后第一个非空段开始取，向下取指定数量。
        - 每个 cell 的 paragraph_indices: 列出落在该 cell Range 内的所有段落索引（从 1 开始）。
        """
        try:
            # 基础对象
            tables = doc.Tables
            if table_index < 1 or table_index > tables.Count:
                raise IndexError(f"table_index {table_index} out of range (1..{tables.Count})")

            table = tables(table_index)
            all_paragraphs = list(doc.Paragraphs)  # 1..N, but this is Python list
            total_paras = len(all_paragraphs)

            table_start = table.Range.Start
            table_end = table.Range.End

            # --- 2️⃣ 页码范围 ---
            start_page = table.Range.Information(constants.wdActiveEndPageNumber)
            end_page = table.Range.Duplicate
            end_page.Collapse(constants.wdCollapseEnd)
            end_page_number = end_page.Information(constants.wdActiveEndPageNumber)

            # === 找到文档中“最后一个其 End <= table_start 的段落”（即表格上方紧邻的段落候选） ===
            last_para_before_table_idx = None
            for i, para in enumerate(all_paragraphs, start=1):
                # 找到第一个段落其 End 超过 table_start 时停止，之前的最后一个就是上方段
                if para.Range.End <= table_start:
                    last_para_before_table_idx = i
                else:
                    break

            # === 构造 before_texts：从 last_para_before_table_idx 向上收集最多 `before` 个非空段 ===
            before_texts = []
            if before > 0 and last_para_before_table_idx:
                count = 0
                for idx in range(last_para_before_table_idx, 0, -1):  # 从紧邻上方往上
                    para_text = all_paragraphs[idx - 1].Range.Text.strip()
                    # 过滤纯空/仅回车等段落
                    if para_text:
                        before_texts.append(para_text)
                        count += 1
                    # 收集到 enough 则停止
                    if count >= before:
                        break
                before_texts.reverse()  # 恢复文中顺序

            # === 构造 after_texts：找到第一个 para whose Start >= table_end，然后收集非空段 ===
            first_para_after_table_idx = None
            for i, para in enumerate(all_paragraphs, start=1):
                if para.Range.Start >= table_end:
                    first_para_after_table_idx = i
                    break

            after_texts = []
            if after > 0 and first_para_after_table_idx:
                count = 0
                for idx in range(first_para_after_table_idx, total_paras + 1):
                    para_text = all_paragraphs[idx - 1].Range.Text.strip()
                    if para_text:
                        after_texts.append(para_text)
                        count += 1
                    if count >= after:
                        break

            # === 解析每个单元格：文本 + 包含的段落索引列表 ===
            # 为加速，预先收集每个段落的 (start,end) 元组
            para_ranges = [(p.Range.Start, p.Range.End) for p in all_paragraphs]

            cells_info= []
            for r in range(1, table.Rows.Count + 1):
                for c in range(1, table.Columns.Count + 1):
                    cell = table.Cell(r, c)
                    raw_text = cell.Range.Text
                    # 去掉单元格末尾的特殊字符（回车/单元格标识）
                    text = raw_text.rstrip('\r\x07').strip()

                    cell_start = cell.Range.Start
                    cell_end = cell.Range.End

                    # 找出所有落在 cell 范围内的段落索引（1-based）
                    para_indices = []
                    # 遍历段落范围，若段落的 Start >= cell_start and End <= cell_end 则属于 cell
                    for idx, (pstart, pend) in enumerate(para_ranges, start=1):
                        if pstart >= cell_start and pend <= cell_end:
                            para_indices.append(idx)

                    # 兼容：若没有找到（极少见），尝试使用段落 Start == cell_start 的匹配
                    if not para_indices:
                        for idx, (pstart, pend) in enumerate(para_ranges, start=1):
                            if pstart == cell_start:
                                para_indices.append(idx)
                                break

                    cells_info.append({
                        "row": r,
                        "col": c,
                        "paragraph": text,
                        "paragraph_index": para_indices[0]  # 可能为 [] 或 [n] 或 [n, n+1, ...]
                    })

            return {
                "state":"success",
                "table_index": table_index,
                "page_range": {
                    "start_page": start_page,
                    "end_page": end_page_number
                },
                "cells": cells_info,
                "before_texts": before_texts,
                "after_texts": after_texts,
            }
        except Exception as e:
            print(f"Get Table info error! The detail is {e}")
            return {
                "state":"error",
                "table_index": table_index,
                "error":e
            }

    def __read_column_width(self, table, table_info, col_index: int = 1):
        width = self.__get_column_width(table, col_index)
        width_pt = width.get("width")
        table_info["column_width"]["width"]["value"]["pt"] = self.pt_to_convert(width_pt, "pt")
        table_info["column_width"]["width"]["value"]["mm"] = self.pt_to_convert(width_pt, "mm")
        table_info["column_width"]["width"]["value"]["cm"] = self.pt_to_convert(width_pt, "cm")
        table_info["column_width"]["width"]["value"]["inches"] = self.pt_to_convert(width_pt, "inches")
        table_info["column_width"]["rule"]["value"] = width.get("rule")
        return table_info

    def __read_row_height(self, table, table_info, row_index: int = 1):
        height = self.__get_row_height(table, row_index)
        height_pt = height.get("height")
        table_info["row_height"]["height"]["value"]["pt"] = self.pt_to_convert(height_pt, "pt")
        table_info["row_height"]["height"]["value"]["mm"] = self.pt_to_convert(height_pt, "mm")
        table_info["row_height"]["height"]["value"]["cm"] = self.pt_to_convert(height_pt, "cm")
        table_info["row_height"]["height"]["value"]["inches"] = self.pt_to_convert(height_pt, "inches")
        table_info["row_height"]["rule"]["value"] = height.get("rule")
        return table_info

    def __read_table_width(self, table, table_info):
        width = self.__get_table_width(table)
        width_pt = width.get("width")
        table_info["table_width"]["width"]["value"]["pt"] = self.pt_to_convert(width_pt, "pt")
        table_info["table_width"]["width"]["value"]["mm"] = self.pt_to_convert(width_pt, "mm")
        table_info["table_width"]["width"]["value"]["cm"] = self.pt_to_convert(width_pt, "cm")
        table_info["table_width"]["width"]["value"]["inches"] = self.pt_to_convert(width_pt, "inches")
        table_info["table_width"]["rule"]["value"] = width.get("rule")
        return table_info

    def __read_table_height(self, table, table_info):
        height = self.__get_table_height(table)
        height_pt = height.get("height")
        table_info["table_height"]["height"]["value"]["pt"] = self.pt_to_convert(height_pt, "pt")
        table_info["table_height"]["height"]["value"]["mm"] = self.pt_to_convert(height_pt, "mm")
        table_info["table_height"]["height"]["value"]["cm"] = self.pt_to_convert(height_pt, "cm")
        table_info["table_height"]["height"]["value"]["inches"] = self.pt_to_convert(height_pt, "inches")
        table_info["table_height"]["rule"]["value"] = height.get("rule")
        return table_info

    def __read_text_wrapping(self, table, table_info):
        table_info["text_wrapping"]["value"] = table.Rows.WrapAroundText
        return table_info

    def __read_pagination(self, table, table_info):
        table_info["pagination"]["allow_break_across_pages"]["value"] = table.Rows.AllowBreakAcrossPages
        table_info["pagination"]["repeat_header"]["value"] = table.Rows(1).HeadingFormat if table.Rows.Count > 0 else 0
        table_info["pagination"]["keep_with_next"]["value"] = table.Range.ParagraphFormat.KeepWithNext
        table_info["pagination"]["page_break_before"]["value"] = table.Range.ParagraphFormat.PageBreakBefore
        return table_info

    def __read_table_alignment(self, table, table_info):
        alignment = self.__get_table_alignment(table)
        table_info["alignment"]["horizontal_align"]["value"] = alignment.get("horizontal_align")
        table_info["alignment"]["vertical_align"]["value"] = alignment.get("vertical_align")
        return table_info

    def __read_left_indent(self, table, table_info):
        left_indent = table.Rows.LeftIndent
        table_info["left_indent"]["value"]["pt"] = left_indent
        table_info["left_indent"]["value"]["mm"] = self.pt_to_convert(left_indent, "mm")
        table_info["left_indent"]["value"]["cm"] = self.pt_to_convert(left_indent, "cm")
        table_info["left_indent"]["value"]["inches"] = self.pt_to_convert(left_indent, "inches")
        return table_info

    def __read_cell_horizontal_align(self, table, table_info, row_index=1, col_index=1):
        table_info["cell_horizontal_align"]["value"] = self.__get_cell_vertical_alignment(table, row_index,
                                                                                          col_index).get(
            "cell_vertical_align")
        return table_info

    def read_table_properties(self, doc, table_index, params_list=[], language='zh', *args, **kwargs):

        # print("params_list: ",params_list)
        attribution_dict = {
            "column_width": self.__read_column_width,
            "row_height": self.__read_row_height,
            "cell_horizontal_align": self.__read_cell_horizontal_align,
            "table_width": self.__read_table_width,
            "table_height": self.__read_table_height,
            "text_wrapping": self.__read_text_wrapping,
            "pagination": self.__read_pagination,
            "alignment": self.__read_table_alignment,
            "left_indent": self.__read_left_indent,
        }
        # 加载读取模板
        template = self.config.get("properties_template")
        if language in ['zh', 'en']:
            table_info = copy.deepcopy(template.get(language))
        else:
            table_info = copy.deepcopy(template.get("zh"))
            print("Default Using Chinese")
        try:
            # 字符串转换
            table_index = int(table_index)
            if table_index > 0:
                table = self.get_table(doc, table_index)
            else:
                print("table index must >= 0!")
                raise
            if not params_list:
                # 未指定读取属性范围，默认全都要读取
                params_list = list(attribution_dict.keys())
            else:
                # 指定读取属性范围以后，删除模板中不需要读取的键值对
                for attribution in attribution_dict.keys():
                    if attribution not in params_list:
                        table_info.pop(attribution)

            # 依次获取要读取的属性
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    attribution_info_read_tool = attribution_dict.get(params)
                    table_info = attribution_info_read_tool(table, table_info)

            # 返回成功结果
            return {"state": "success", "properties": table_info}

        except Exception as e:
            # 捕获异常并返回错误信息
            return {"state": "false", "table_index": table_index, "exception": str(e)}
if __name__ == '__main__':
    word = win32.DispatchEx("Word.Application")  # 或使用Dispatch
    word.Visible = True  # 设为可见（调试时建议开启）
    word_file_path = "./file/Word_test.docx"
    word_file_path = os.path.join(ABS_DIR, word_file_path)
    # print(word_file_path)
    # 打开已有文档
    try:
        # 打开文档
        doc = word.Documents.Open(word_file_path)
        table_reader = TableReader()
        table = doc.Tables(1)

        # table.PreferredWidthType = constants.wdPreferredWidthPoints
        # table.PreferredWidth = 400
        # table.AllowAutoFit = False

        # doc.Save()
        # print(table.Cell(1, 1).Width)
        # print(table.Columns(1).Width)
        # print(table_reader.read_table_properties(doc,1,['column_width','table_height','table_width','row_height']))

        # print(doc.Tables(2).PreferredWidth)
        # print(table_reader.read_table_properties(doc, 1, []))
        # print(table_reader.read_table_properties(doc, 2, []))

        # print(table_reader.get_table_infos(doc,1,1,1))

        print(table_reader.get_tables_format(doc))

    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        # 确保清理资源
        if 'doc' in locals():
            doc.Close(SaveChanges=False)
        word.Quit()