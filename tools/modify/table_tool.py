import win32com.client as win32
from win32com.client import constants
import os
from tools.modify.tool_config import ContextToolsConfig

class TableBaseTools():
    def __init__(self):
        # 在初始化时创建一次，重复使用
        self.excel_app = win32.Dispatch("Excel.Application")

    def convert_to_pt(self, value, unit):
        execl = self.excel_app
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit in ["pt","point","磅"]:
            return float(value)
        elif unit in ["cm","centimeter","厘米"]:
            return execl.CentimetersToPoints(value)
        elif unit in ["mm","millimeter","毫米"]:
            return execl.CentimetersToPoints(value*0.1)
        elif unit in ["inches","英寸"]:
            return execl.InchesToPoints(value)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def set_row_height(self, table, row_index: int, height: float, unit: str = "cm", rule: str = "exactly"):
        """
        设置表格中某一行的高度（影响该行所有单元格）

        :param table: Word 表格对象
        :param row_index: 行号，0 表示所有行，1 表示第一行
        :param height: 高度值
        :param unit: 单位，可选值：
                     "cm" - 厘米
                     "mm" - 毫米
                     "point" - 磅
                     "inches" - 英寸
        :param rule: 高度规则，可选值：
                     "auto" - 自动调整
                     "at_least" - 最小高度
                     "exactly" - 固定高度
        """

        try:
            # 单位换算：统一转为 point (磅)
            if rule == "auto":
                height_pt = 0
            else:
                height_pt = self.convert_to_pt(value=height,unit=unit)

            # 高度规则映射
            rule_map = {
                "auto": constants.wdRowHeightAuto,
                "at_least": constants.wdRowHeightAtLeast,
                "exactly": constants.wdRowHeightExactly
            }

            if rule not in rule_map:
                raise ValueError("高度规则必须是 auto/at_least/exactly")

            # 设置行高
            if row_index == 0:  # 所有行
                for row in table.Rows:
                    row.HeightRule = rule_map[rule]
                    if rule != "auto":
                        row.Height = height_pt
                target_desc = f"all rows -> {height} {unit} ({rule})"
            else:  # 指定行
                row = table.Rows(row_index)
                row.HeightRule = rule_map[rule]
                if rule != "auto":
                    row.Height = height_pt
                target_desc = f"row {row_index} -> {height} {unit} ({rule})"

            return {
                    "status": "success",
                    "message": f"Set {target_desc}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_column_width(self, table, col_index: int, width: float, unit: str = "cm", rule: str = "exactly"):
        """
        设置表格中某一列的宽度（影响该列所有单元格）

        :param table: Word 表格对象
        :param col_index: 列号，0 表示所有列，1 表示第一列
        :param width: 宽度值
        :param unit: 单位，可选值：
                     "cm" - 厘米
                     "mm" - 毫米
                     "point" - 磅
                     "inches" - 英寸
        :param rule: 宽度规则，可选值：
                     "auto" - 自动调整
                     "at_least" - 最小宽度（Word 无严格支持）
                     "exactly" - 固定宽度
        """
        try:
            # 单位换算：统一为 point (磅)
            if rule == "auto":
                width_pt = 0
            else:
                width_pt = self.convert_to_pt(value=width, unit=unit)

            # 宽度规则映射
            # Word 里没有 RowHeightRule 对应的列宽规则，只能通过 PreferredWidthType 控制
            width_type_map = {
                "auto": constants.wdPreferredWidthAuto,
                "exactly": constants.wdPreferredWidthPoints
            }

            if rule not in width_type_map and rule != "at_least":
                raise ValueError("width rule must be auto/at_least/exactly")

            # 设置列宽
            if col_index == 0:  # 所有列
                for col in table.Columns:
                    if rule == "auto":
                        col.PreferredWidthType = width_type_map["auto"]
                        col.PreferredWidth = 0
                    else:
                        col.PreferredWidthType = width_type_map.get(rule, constants.wdPreferredWidthPoints)
                        col.PreferredWidth = width_pt
                target_desc = f"all columns -> {width} {unit} ({rule})"

            else:  # 指定列
                col = table.Columns(col_index)
                if rule == "auto":
                    col.PreferredWidthType = width_type_map["auto"]
                    col.PreferredWidth = 0
                else:
                    col.PreferredWidthType = width_type_map.get(rule, constants.wdPreferredWidthPoints)
                    col.PreferredWidth = width_pt
                target_desc = f"column {col_index} -> {width} {unit} ({rule})"

            return {
                    "status": "success",
                    "message": f"Set {target_desc}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_allow_break_across_pages(self, table, allow_break):
        """
        设置表格是否允许跨页拆分行
        """
        try:
            table.Rows.AllowBreakAcrossPages = allow_break
            return {
                    "status": "success",
                    "message": f"Set allow_break_across_pages = {allow_break}"
            }
        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_repeat_header(self, table, repeat):
        """
        设置表格是否重复显示标题行（页眉行）
        """
        try:
            if table.Rows.Count > 0:
                table.Rows(1).HeadingFormat = repeat

            return {
                    "status": "success",
                    "message": f"Set repeat_header = {repeat}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_keep_with_next(self, table, keep):
        """
        设置表格段落“与下一段同页”
        """
        try:
            table.Range.ParagraphFormat.KeepWithNext = keep
            return {
                    "status": "success",
                    "message": f"Set keep_with_next = {keep}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_page_break_before(self, table, enable):
        """
        设置表格前是否分页
        """
        try:
            table.Range.ParagraphFormat.PageBreakBefore = enable
            return {
                    "status": "success",
                    "message": f"Set page_break_before = {enable}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_table_width(self, table, width: float, unit: str = "cm", rule: str = "fixed"):
        """
        设置表格整体宽度及列宽调整规则

        :param table: Word 表格对象
        :param width: 宽度值
        :param unit: 单位，可选："cm"、"mm"、"inches"、"pt"、"percent"
        :param rule: 列宽调整规则，可选：
                     "fixed" - 固定列宽（默认）
                     "auto_content" - 根据内容自动调整
                     "auto_window" - 根据窗口自动调整
        """
        try:
            # ---- 宽度设置 ----
            if unit in ["cm", "厘米", "mm", "毫米", "inches", "英寸"]:
                table.PreferredWidth = self.convert_to_pt(width, unit=unit)
                table.PreferredWidthType = constants.wdPreferredWidthPoints
                desc = f"{width}{unit}"
            elif unit in ["pt", "磅"]:
                table.PreferredWidth = width
                table.PreferredWidthType = constants.wdPreferredWidthPoints
                desc = f"{width} pt"
            elif unit in ["percent", "百分比"]:
                table.PreferredWidth = width
                table.PreferredWidthType = constants.wdPreferredWidthPercent
                desc = f"{width}%"
            else:
                raise ValueError("Width unit must be one of cm/mm/inches/pt/percent")

            # ---- 宽度规则设置 ----
            rule_map = {
                "fixed": constants.wdAutoFitFixed,
                "auto_content": constants.wdAutoFitContent,
                "auto_window": constants.wdAutoFitWindow
            }

            if rule not in rule_map:
                raise ValueError(f"Invalid rule: {rule}, must be one of {list(rule_map.keys())}")

            table.AutoFitBehavior(rule_map[rule])

            return {
                "status": "success",
                "message": f"Set table width to {desc}, rule={rule}"
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    def set_table_height(self, table, height: float = 0, unit: str = "cm", rule: str = "exactly"):
        """
        设置表格整体高度（通过平均分配行高实现）

        :param table: Word 表格对象
        :param height: 表格总高度，若为 0 则不调整
        :param unit: 单位，可选："cm"、"mm"、"inches"、"pt"
        :param rule: 高度规则，可选：
                     "exactly" - 固定高度（默认）
                     "at_least" - 最小高度
                     "auto" - 自动高度（不设置具体值）
                     "auto_content" - 根据内容自动调整
        """
        try:
            # 空表格直接返回
            if table.Rows.Count == 0:
                raise ValueError("Table has no rows, cannot set height")

            # rule 映射
            rule_mapping = {
                "exactly": constants.wdRowHeightExactly,  # 固定高度
                "at_least": constants.wdRowHeightAtLeast,  # 最小高度
                "auto": constants.wdRowHeightAuto  # 自动高度
            }

            # ---- 自动内容模式：使用 AutoFitBehavior ----
            if rule == "auto_content":
                table.AutoFitBehavior(constants.wdAutoFitContent)
                return {
                    "status": "success",
                    "message": "Set table height to auto adjust based on content"
                }

            # ---- 手动设置高度 ----
            if height <= 0:
                return {
                    "status": "success",
                    "message": "Height set to auto (no manual adjustment)"
                }

            if rule not in rule_mapping:
                raise ValueError(f"Height rule must be one of {list(rule_mapping.keys())} or 'auto_content'")

            rule_constant = rule_mapping[rule]
            height_pt = self.convert_to_pt(height, unit=unit)
            row_height = height_pt / table.Rows.Count

            for row in table.Rows:
                row.HeightRule = rule_constant
                if rule != "auto":
                    row.Height = row_height

            rule_desc = {
                "exactly": "fixed",
                "at_least": "minimum",
                "auto": "auto"
            }.get(rule, "auto")

            message = f"Set table {rule_desc} height = {height} {unit} (each row {row_height:.2f} pt)"

            return {
                "status": "success",
                "message": message
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    def set_cell_vertical_alignment(self, cell, alignment: str):
        """
        设置单元格垂直对齐方式

        参数：
            cell: Word 单元格对象
            alignment (str): 垂直对齐方式，可选值：
                             "top" / "center" / "bottom"

        返回：
            {
                "vertical_alignment": {
                    "status": "success" / "error",
                    "message": "操作结果描述"
                }
            }
        """
        try:
            # 定义对齐方式映射（Word常量）
            alignment_map = {
                "TOP": 0,  # wdCellAlignVerticalTop
                "CENTER": 1,  # wdCellAlignVerticalCenter
                "BOTTOM": 3,  # wdCellAlignVerticalBottom
            }

            # 获取对齐值
            alignment_key = alignment_map.get(alignment.upper())
            if alignment_key is None:
                raise ValueError("Error Vertical Alignment, must be 'top', 'center' or 'bottom'")

            # 设置对齐
            cell.VerticalAlignment = alignment_key

            return {
                    "status": "success",
                    "message": f"Cell vertical alignment set to {alignment.upper()}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_table_alignment(self, table, alignment: str):
        """
        设置表格整体的水平对齐方式（相对于页面）
        参数：
            table: Word 表格对象
            alignment (str): 表格对齐方式，可选：
                             "left" / "center" / "right"
        返回：
            {
                "table_alignment": {
                    "status": "success" / "error",
                    "message": "操作结果描述"
                }
            }
        """
        try:
            # 对齐方式映射（Word常量）
            alignment_map = {
                "LEFT": 0,  # wdAlignParagraphLeft
                "CENTER": 1,  # wdAlignParagraphCenter
                "RIGHT": 2,  # wdAlignParagraphRight
                "左对齐": 0,
                "居中": 1,
                "右对齐":2,
                "CENTERED":1,
            }

            align_key = alignment_map.get(alignment.upper())
            if align_key is None:
                raise ValueError("Error Alignment Type，must be 'left'、'center' or 'right'")

            # 设置表格整体对齐方式
            table.Rows.Alignment = align_key

            return {
                    "status": "success",
                    "message": f"Table alignment set to {alignment.upper()}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_table_text_wrapping(self, table, wrapping_style: int = 0):
        """
        设置表格文字环绕方式

        :param table: Word 表格对象
        :param wrapping_style: 环绕方式，可选：
                              "none" - 无环绕（默认）
                              "around" - 文字环绕
        """
        try:
            table.Rows.WrapAroundText = wrapping_style
            style_desc = "no paragraph wrapping" if wrapping_style == 0 else "paragraph wrapping around"
            return {
                "status": "success",
                "message": f"Set table paragraph wrapping to '{wrapping_style}' ({style_desc})"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"0 for no paragraph wrapping and -1 for paragraph wrapping around. The detail is {e}"
            }

    def set_table_left_indent(self, table, indent: float, unit: str = "cm"):
        """
        设置表格左缩进

        :param table: Word 表格对象
        :param indent: 缩进距离
        :param unit: 单位，可选："cm" - 厘米,"pt" - 磅,"mm" - 毫米,"inches" - 英寸,
        """
        try:
            indent_pt = self.convert_to_pt(indent,unit)
            if indent_pt > 0:
                # 首先将表格对齐方式设置为左对齐，这样才能设置左缩进
                table.Rows.Alignment = 0  # 0 = wdAlignRowLeft
            # 设置表格左缩进
            table.Rows.LeftIndent = indent_pt
            return {
                "status": "success",
                "message": f"Set table left indent to {indent} {unit} ({indent_pt:.2f} pt)"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

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

    def get_cell(self, table, row_index, column_index):
        try:
            # Check if table object is valid
            if table is None:
                raise ValueError("Table object cannot be None")

            # Check if table object has Rows and Columns attributes
            if not hasattr(table, 'Rows') or not hasattr(table, 'Columns'):
                raise ValueError("The provided object is not a valid Word table object")

            # Validate row index
            if not isinstance(row_index, int) or row_index < 1:
                raise ValueError(f"Row index must be a positive integer, got: {row_index}")

            # Validate column index
            if not isinstance(column_index, int) or column_index < 1:
                raise ValueError(f"Column index must be a positive integer, got: {column_index}")

            # Check if row index is within range
            if row_index > table.Rows.Count:
                raise ValueError(f"Row index {row_index} is out of range. Table contains {table.Rows.Count} row(s)")

            # Check if column index is within range
            if column_index > table.Columns.Count:
                raise ValueError(
                    f"Column index {column_index} is out of range. Table contains {table.Columns.Count} column(s)")

            # Get specified cell (Word cell indexing starts from 1)
            cell = table.Cell(row_index, column_index)

            # Validate cell object
            if cell is None:
                raise Exception(f"Retrieved cell object is None for position: ({row_index}, {column_index})")

            return cell

        except ValueError as ve:
            # Re-raise ValueError with original type
            raise ve
        except Exception as e:
            # Wrap other exceptions with clearer error message
            error_msg = f"Failed to get cell (row:{row_index}, column:{column_index}): {str(e)}"
            raise Exception(error_msg) from e


class TableTools():
    def __init__(self,):
        self.tableTool = TableBaseTools()

    def __set_row_height(self, doc, table_index, row_index=0, height=1.0, unit="cm", rule="at_least"):
        table = self.tableTool.get_table(doc,table_index)
        status = self.tableTool.set_row_height(table,row_index=row_index,height=height,unit=unit,rule=rule)
        return {"row_height":status}

    def __set_column_width(self, doc, table_index, col_index=0, width=3.0, unit="cm",rule = "auto"):
        table = self.tableTool.get_table(doc, table_index)
        status = self.tableTool.set_column_width(table=table,col_index=col_index,width=width,unit=unit,rule=rule)
        return {"column_width":status}

    def __set_table_pagination(self, doc, table_index, allow_break_across_pages=None, repeat_header=None,
                             keep_with_next=None, page_break_before=None):
        table = self.tableTool.get_table(doc,table_index)
        result = {}
        if allow_break_across_pages is not None:
            status = self.tableTool.set_allow_break_across_pages(table,allow_break_across_pages)
            result["allow_break_across_page"] = status
        if repeat_header is not None:
            status = self.tableTool.set_repeat_header(table,repeat_header)
            result["repeat_header"] = status
        if keep_with_next is not None:
            status = self.tableTool.set_keep_with_next(table,keep_with_next)
            result["keep_with_next"] = status
        if page_break_before is not None:
            status = self.tableTool.set_page_break_before(table,page_break_before)
            result["page_break_before"] = status
        return result

    def __set_table_height(self,doc, table_index, height: float, unit: str = "cm",rule="exactly"):
        table = self.tableTool.get_table(doc,table_index)
        status = self.tableTool.set_table_height(table,height,unit,rule)
        return {"table_height":status}

    def __set_table_width(self,doc, table_index, width:float, unit:str ="cm",rule = "fixed"):
        table = self.tableTool.get_table(doc, table_index)
        status = self.tableTool.set_table_width(table, width, unit,rule)
        return {"table_width": status}

    def __set_cell_vertical_alignment(self, doc, table_index, row_index, col_index, alignment: str):
        table = self.tableTool.get_table(doc,table_index)
        cell = self.tableTool.get_cell(table,row_index,col_index)
        status = self.tableTool.set_cell_vertical_alignment(cell,alignment)
        return {"cell_vertical_alignment":status}

    def __set_table_vertical_alignment(self,doc, table_index,alignment: str):
        table = self.tableTool.get_table(doc, table_index)
        for i in range(1, table.Rows.Count + 1):
            for j in range(1, table.Columns.Count + 1):
                cell = table.Cell(i, j)
                status = self.tableTool.set_cell_vertical_alignment(cell,alignment)
        return {"table_alignment": status}

    def __set_table_horizontal_alignment(self,doc,table_index,alignment:str):
        table = self.tableTool.get_table(doc, table_index)
        status = self.tableTool.set_table_alignment(table, alignment)
        return {"table_alignment": status}


    def __set_table_alignment(self,doc, table_index, horizontal_align=None, vertical_align=None):
        results = {}
        if horizontal_align is not None:
            results["horizontal_alignment"] = self.__set_table_horizontal_alignment(doc,table_index,horizontal_align)
        if vertical_align is not None:
            results["vertical_alignment"] = self.__set_table_vertical_alignment(doc, table_index, vertical_align)
        return results

    def __set_table_left_indent(self, doc, table_index, indent:float, unit:str = "cm"):
        table = self.tableTool.get_table(doc, table_index)
        status = self.tableTool.set_table_left_indent(table,indent,unit)
        return {"table_left_indent": status}

    def __set_table_text_wrapping(self,doc,table_index, wrapping_style:int=0):
        table = self.tableTool.get_table(doc,table_index)
        status = self.tableTool.set_table_text_wrapping(table,wrapping_style)
        return {"text_wrapping":status}

    def set_column_width(self, doc, location_list, col_index=1, width=1.0, unit="cm", rule="at_least"):
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_column_width(doc=doc,table_index=table_index,col_index=col_index,width=width,unit=unit,rule=rule)
                results = status
            doc.Save()
        except Exception as e:
            results["column_width"] = {
                "status": "error",
                "message": f"Failed to set row height, the detail is : {e}"}
        finally:
            return results

    def set_row_height(self,doc, location_list, row_index=1, height=1.0, unit="cm", rule="at_least"):
        results = {}
        if 'all' in location_list:
            location_list = [i+1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_row_height(doc=doc,table_index=table_index,row_index=row_index,height=height,unit=unit,rule=rule)
                results = status
            doc.Save()
        except Exception as e:
            results["row_height"] = {
                "status": "error",
                "message": f"Failed to set row height, the detail is : {e}"}
        finally:
            return results

    def set_table_width(self, doc, location_list, width:float, unit:str ="cm",rule = "fixed"):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_width(doc=doc, table_index=table_index, width=width,unit=unit,rule=rule)
                results = status
            doc.Save()
        except Exception as e:
            results["table_width"] = {
                "status": "error",
                "message": f"Failed to set row height, the detail is : {e}"}
        finally:
            return results

    def set_table_height(self,doc, location_list, height, unit:str = 'cm',rule="exactly"):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_height(doc=doc, table_index=table_index, height=height, unit=unit,rule=rule)
                results = status
            doc.Save()
        except Exception as e:
            results["row_height"] = {
                "status": "error",
                "message": f"Failed to set row height, the detail is : {e}"}
        finally:
            return results

    def set_table_text_wrapping(self,doc, location_list, wrapping_style=0):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_text_wrapping(doc=doc, table_index=table_index, wrapping_style=wrapping_style)
                results = status
            doc.Save()
        except Exception as e:
            results["text_wrapping"] = {
                "status": "error",
                "message": f"Failed to set text_wrapping, the detail is : {e}"}
        finally:
            return results

    def set_table_pagination(self, doc, location_list, allow_break_across_pages=None, repeat_header=None,
                             keep_with_next=None, page_break_before=None):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_pagination(doc=doc, table_index=table_index, allow_break_across_pages=allow_break_across_pages, repeat_header=repeat_header,
                             keep_with_next=keep_with_next, page_break_before=page_break_before)
                results = status
            doc.Save()
        except Exception as e:
            results["text_wrapping"] = {
                "status": "error",
                "message": f"Failed to set text_wrapping, the detail is : {e}"}
        finally:
            return results

    def set_table_alignment(self, doc, location_list, horizontal_align,vertical_align):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_alignment(doc,table_index,horizontal_align=horizontal_align,vertical_align=vertical_align)
                results = status
            doc.Save()
        except Exception as e:
            results["table_alignment"] = {
                "status": "error",
                "message": f"Failed to set table alignment, the detail is : {e}"}
        finally:
            return results

    def set_table_left_indent(self, doc, location_list, indent: float, unit: str = "cm"):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_table_left_indent(doc=doc,table_index=table_index,indent=indent,unit=unit)
                results = status
            doc.Save()
        except Exception as e:
            results["table_left_indent"] = {
                "status": "error",
                "message": f"Failed to set table alignment, the detail is : {e}"}
        finally:
            return results

    def set_cell_vertical_alignment(self, doc, location_list, row_index, col_index, alignment: str):
        results = {}
        if 'all' in location_list:
            location_list = [i + 1 for i in range(doc.Tables.Count)]
        try:
            for table_index in location_list:
                status = self.__set_cell_vertical_alignment(doc=doc,table_index=table_index,row_index=row_index, col_index=col_index, alignment=alignment)
                results = status
            doc.Save()
        except Exception as e:
            results["cell_vertical_alignment"] = {
                "status": "error",
                "message": f"Failed to set table alignment, the detail is : {e}"}
        finally:
            return results

    def set_format(self, doc, location_list, settings):
        support_functions = {
            "table_width": self.set_table_width,
            "table_height": self.set_table_height,
            "text_wrapping": self.set_table_text_wrapping,
            "pagination": self.set_table_pagination,
            "table_alignment": self.set_table_alignment,
            "left_indent": self.set_table_left_indent,
        }
        support_properties = list(support_functions.keys())
        for support_property in support_properties:
            property_setting = settings.get(support_property)
            if property_setting:
                property_function = support_functions.get(support_property)
                property_function(doc,location_list,**property_setting)