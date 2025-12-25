import yaml
from tools.llm.openai_client import OpenaiLLMClient
from constant import ABS_DIR
import os,shutil,itertools,re
from tools.modify.page_tool import PageTools
from tools.modify.paragraph_tool import TextTools
from tools.modify.table_tool import TableTools
from tools.modify.image_tool import ImageTools
from tools.reader.page_reader import PageReader
from tools.reader.text_reader import TextReader
from tools.reader.table_reader import TableReader
from tools.reader.image_reader import ImageReader
from tools.reader.file_trans import FileConverter
import win32com.client as win32
from tools.modify.tool_config import ContextToolsConfig
from data_contribution.compare import DictComparator
from win32com.client import gencache

class FormatReader():
    def __init__(self):
        self.page_reader = PageReader()
        self.text_reader = TextReader()
        self.table_reader = TableReader()
        self.image_reader = ImageReader()

    def read_template_page(self,doc):
        return self.page_reader.get_page_styles(doc)

    def read_template_page_description(self, doc, format_params_list, language = 'zh'):
        format_properties = []
        for format_params in format_params_list:
            # print("format_params:",format_params)
            params_list = list(format_params.get("settings").keys())
            section_index = format_params.get("section_list")[0]
            format_description = self.page_reader.read_page_properties(doc,section_index=section_index,params_list=params_list,language=language)
            # print("format_description:",format_description)
            if format_description.get("state") == "success":
                description = self.remove_extract_description(format_dict=format_params.get("settings"),exclude_keys=[],
                                                              description_dict=format_description.get("properties"))
                format_properties.append({"section_style":format_params.get("style_name"),"format_description":description})
        return format_properties

    def read_template_text(self,doc):
        return self.text_reader.get_paragraphs_format(doc)

    def read_template_paragraph_description(self, doc, format_params_list, language = 'zh'):
        format_properties = []
        for format_params in format_params_list:
            params_list = list(format_params.get("settings").keys())
            # print(params_list)
            paragraph_index = format_params.get("paragraph_list")[0]
            format_description = self.text_reader.read_text_properties(doc=doc,paragraph_index=paragraph_index,params_list=params_list,language=language)
            # print(format_description)
            if format_description.get("state") == "success":
                description = self.remove_extract_description(format_dict=format_params.get("settings"), exclude_keys=['alignment', 'outlinelevel'],
                                                        description_dict=format_description.get("properties"))
                format_properties.append({"paragraph_style":format_params.get("style_name"),"format_description":description})
        return format_properties

    def read_template_table(self,doc):
        return self.table_reader.get_tables_format(doc)


    def read_template_table_description(self, doc, format_params_list, language = 'zh'):
        format_properties = []
        for format_params in format_params_list:
            params_list = list(format_params.get("settings").keys())
            # print(params_list)
            table_index = format_params.get("table_list")[0]
            format_description = self.table_reader.read_table_properties(doc,table_index=table_index,params_list=params_list,language=language)
            if format_description.get("state") == "success":
                description = self.remove_extract_description(format_dict=format_params.get("settings"),
                                                              exclude_keys=['cell_horizontal_align', 'text_wrapping'],
                                                              description_dict=format_description.get("properties"))
                format_properties.append(
                    {"style_name": format_params.get("style_name"), "format_description": description})
        return format_properties


    def read_template_image(self,doc):
        return self.image_reader.get_images_format(doc)

    def read_template_image_description(self, doc, format_params_list, language = 'zh'):
        format_properties = []
        for format_params in format_params_list:
            params_list = list(format_params.get("settings").keys())
            # print(params_list)
            image_index = format_params.get("image_list")[0]
            format_description = self.image_reader.read_image_properties(doc,image_index=image_index,params_list=params_list,language=language)
            if format_description.get("state") == "success":
                description = self.remove_extract_description(format_dict=format_params.get("settings"),
                                                              exclude_keys=['alignment'],
                                                              description_dict=format_description.get("properties"))
                format_properties.append(
                    {"style_name": format_params.get("style_name"), "format_description": description})
        return format_properties

    def remove_same_property(self, base_dict, compared_dict):
        different_dict = {}
        for key, comp_value in compared_dict.items():
            # 如果base_dict中没有这个key，直接记录
            if key not in base_dict:
                different_dict[key] = comp_value
            else:
                base_value = base_dict[key]
                # 如果都是字典，递归比较
                if isinstance(comp_value, dict) and isinstance(base_value, dict):
                    nested_diff = self.remove_same_property(base_value, comp_value)
                    # 只有当嵌套字典有不同时才记录
                    if nested_diff:
                        different_dict[key] = nested_diff
                else:
                    # 其他类型直接比较值，如果不同就记录
                    if base_value != comp_value:
                        different_dict[key] = comp_value
        return different_dict

    def remove_extract_description(self,format_dict, description_dict, exclude_keys):
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

class FormatTool():
    def __init__(self):
        self.page_tool = PageTools()
        self.text_tool = TextTools()
        self.image_tool = ImageTools()
        self.table_tool = TableTools()


    def __set_section(self, doc, section_list, format_settings):
        self.page_tool.set_format(doc=doc,location_list=section_list,settings=format_settings)

    def __set_paragraph(self, doc, paragraph_list, format_settings):
        self.text_tool.set_format(doc,location_list=paragraph_list,settings=format_settings)

    def __set_table(self, doc, table_list, format_settings):
        self.table_tool.set_format(doc,location_list=table_list,settings=format_settings)

    def __set_image(self, doc, image_list, format_settings):
        self.image_tool.set_format(doc,location_list=image_list,settings=format_settings)

    def set_sections(self, doc, format_dict, style_map):
        for style_name in format_dict.keys():
            format_settings = format_dict.get(style_name)
            location_list = style_map.get(style_name)
            self.__set_section(doc,location_list,format_settings)

    def set_paragraphs(self, doc, format_dict, style_map):
        for style_name in format_dict.keys():
            format_settings = format_dict.get(style_name)
            location_list = style_map.get(style_name)
            self.__set_paragraph(doc, location_list, format_settings)

    def set_tables(self, doc, format_dict, style_map):
        for style_name in format_dict.keys():
            format_settings = format_dict.get(style_name)
            location_list = style_map.get(style_name)
            self.__set_table(doc, location_list, format_settings)

    def set_images(self, doc, format_dict, style_map):
        for style_name in format_dict.keys():
            format_settings = format_dict.get(style_name)
            location_list = style_map.get(style_name)
            self.__set_image(doc, location_list, format_settings)

    def reset_template_page(self, doc_path, format_list):
        word = win32.DispatchEx("Word.Application")
        word.ActivePrinter = "Microsoft Print to PDF"  # 指定打印机
        word.Visible = True  # 设为可见（调试时建议开启）
        doc = word.Documents.Open(doc_path)
        for format_dict in format_list:
            format_settings = format_dict.get("settings")
            section_list = format_dict.get("section_list")
            self.__set_section(doc=doc, section_list=section_list, format_settings=format_settings)
        word.Quit()

class ParagraphStyleMap():
    def __init__(self):
        self.filetool = FileConverter()
    def filter_docx_files(self, directory_path: str) -> list[str]:
        # ... [省略了路径检查和打印信息]
        docx_files = []
        # 关键点：os.listdir() 只列出当前目录的内容
        for item_name in os.listdir(directory_path):
            # 1. 检查 item_name 是否是文件
            item_path = os.path.join(directory_path, item_name)

            # 2. 只有当它是一个文件时，才检查扩展名
            if os.path.isfile(item_path):  # 这一步确保我们忽略了所有子文件夹
                if item_name.lower().endswith('.docx'):
                    docx_files.append(item_name)
        return docx_files

    def __extract_paragraph_style(self, text):
        """
        匹配字符串开头是否存在{xxxx}格式，如果有则提取xxxx返回，否则返回'other'
        规则：
        1. 只匹配字符串最开头
        2. 必须以"{"开头
        3. 提取第一个"{"和第一个"}"之间的内容
        4. 确保输入的字符串是文本，去除可能的换行、回车等空白符
        5. 如果没有匹配到，返回'other'
        参数:
            text: 输入的字符串
        返回:
            如果匹配到{xxxx}格式，返回xxxx内容，否则返回'other'
        """
        if not isinstance(text, str):
            return 'other'

        # 去除字符串开头和结尾的空白字符（包括换行、回车、制表符等）
        text = text.strip()

        # 精确匹配以{开头，然后是非贪婪匹配直到遇到第一个}
        pattern = r'^\{(.*?)\}'
        match = re.match(pattern, text)
        if match:
            # 提取大括号内的内容
            return match.group(1)
        else:
            return 'other'

    def __convert_map(self, style_list):
        style_dict = {}
        for style in style_list:
            style_name = style.get("style_name")
            para_index = style.get("index")
            if style_name in style_dict:
                style_dict[style_name].append(para_index)
            else:
                style_dict[style_name] = [para_index]
        return style_dict

    def __extract_docx_styles(self, doc):
        style_list = []
        for para_index in range(1, doc.Paragraphs.Count + 1):
            paragraph = doc.Paragraphs(para_index)
            text = paragraph.Range.Text.strip()
            style = self.__extract_paragraph_style(text)
            style_list.append({"index": para_index, "style_name": style})
        style_map = self.__convert_map(style_list)
        return style_map

    def __get_docx_map(self, doc_path):
        try:
            style_map = {}
            # 连接Word
            word = win32.DispatchEx("Word.Application")  # 或使用Dispatch
            # print(word.ActivePrinter)
            word.ActivePrinter = "Microsoft Print to PDF"
            word.Visible = True  # 设为不可见（调试时建议开启）
            doc = word.Documents.Open(os.path.join(ABS_DIR, doc_path))
            style_map = self.__extract_docx_styles(doc)
        except Exception as e:
            print(f"操作失败：{str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                doc.Close(SaveChanges=False)
            word.Quit()
            return style_map

    def get_paragraph_style_map(self, area_list=[]):
        data_dir = os.path.join(ABS_DIR, "data_contribution/template/content")
        languages = [
            'zh',
            'en'
        ]
        for language in languages:
            for area in area_list:
                area_dir = os.path.join(data_dir, f"{language}/{area}")
                content_template_list = self.filter_docx_files(area_dir)
                for content_name in content_template_list:
                    template_file = os.path.join(area_dir, content_name)
                    paragraphs_map = self.__get_docx_map(template_file)
                    self.filetool.write_json_file(paragraphs_map,
                                                  os.path.join(area_dir, f"{template_file}.json"))

class DataContribution():
    def __init__(self, pyconfig=ContextToolsConfig(config_path="data_contribution/config/page_requirement_generate.yaml")):
        self.format_reader = FormatReader()
        self.format_tool = FormatTool()
        self.file_tool = FileConverter()
        # print(pyconfig.config)
        self.prompt_config = pyconfig.config.get("prompt_config")

        self.llm = OpenaiLLMClient(pyconfig.config.get("llm_config"))

    def __get_base_format(self, base_dir, mode="page", base_name = "base.docx"):
        base_format_path = os.path.join(ABS_DIR, base_dir, "base_params.json")
        if os.path.exists(base_format_path):
            base_format = self.file_tool.read_json_file(base_format_path)
        else:
            try:
                word = win32.DispatchEx("Word.Application")
                word.Visible = True  # 设为可见（调试时建议开启）
                base_path = os.path.join(ABS_DIR, base_dir, base_name)
                base_doc = word.Documents.Open(base_path)
                read_format_dict = {
                    "page": self.format_reader.read_template_page,
                    "paragraph": self.format_reader.read_template_text,
                    "image": self.format_reader.read_template_image,
                    "table": self.format_reader.read_template_table,
                }
                read_function = read_format_dict.get(mode)
                base_format = read_function(base_doc)
                # print(base_format)
                self.file_tool.write_json_file(base_format,file_path=base_format_path)
            except Exception as e:
                print(f"Error in base {mode} params get! The details is: {str(e)}")
                raise
            finally:
                # 确保清理资源
                if 'doc' in locals():
                    base_doc.Close(SaveChanges=False)
                word.Quit()
        return base_format

    def __init_template_page_params(self,base_dir, template_dir,base_name = "base.docx",template_name = "template.docx"):
        word = win32.DispatchEx("Word.Application")
        word.Visible = True  # 设为可见（调试时建议开启）
        try:
            # 打开文档
            template_path = os.path.join(template_dir,template_name)
            template_doc = word.Documents.Open(template_path)
            # base_format有且仅有一个
            base_format = self.__get_base_format(base_dir=base_dir,mode="page", base_name=base_name)[0]["format_properties"]
            template_format_list = self.format_reader.read_template_page(template_doc)
            format_list = []

            for i,template_format in enumerate(template_format_list):
                template = template_format.get("format_properties")
                format_dict = self.format_reader.remove_same_property(base_dict=base_format,compared_dict=template)
                format_list.append({"style_name":i,"section_list":template_format.get("section_list"),"settings":format_dict})

            # print(template_format_list)
            # print(base_format)
            params_path = os.path.join(template_dir, "params.json")
            self.file_tool.write_json_file(format_list, file_path=params_path)
            # print(format_list)
            return format_list

        except Exception as e:
            print(f"Error in template page params get! The details is: {str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_paragraph_params(self,base_dir, template_dir,base_name = "base.docx",template_name = "template.docx"):
        word = win32.DispatchEx("Word.Application")
        word.Visible = True  # 设为可见（调试时建议开启）
        try:
            # 打开文档
            template_path = os.path.join(template_dir,template_name)
            template_doc = word.Documents.Open(template_path)
            # base_format有且仅有一个
            base_format = self.__get_base_format(base_dir=base_dir,mode="paragraph", base_name=base_name)[0]["format_properties"]
            template_format_list = self.format_reader.read_template_text(template_doc)
            format_list = []

            for i,template_format in enumerate(template_format_list):
                template = template_format.get("format_properties")
                format_dict = self.format_reader.remove_same_property(base_dict=base_format,compared_dict=template)
                format_list.append({"style_name":template_format.get("style_name"),
                                    "paragraph_list":template_format.get("paragraph_list"),"settings":format_dict})

            # print(template_format_list)
            # print(base_format)
            params_path = os.path.join(template_dir, "params.json")
            self.file_tool.write_json_file(format_list, file_path=params_path)
            # print(format_list)
            return format_list

        except Exception as e:
            print(f"Error in template paragraph style params get! The details is: {str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_table_params(self, base_dir, template_dir, base_name="base.docx", template_name="template.docx"):
        word = win32.DispatchEx("Word.Application")
        word.Visible = True  # 设为可见（调试时建议开启）
        try:
            # 打开文档
            template_path = os.path.join(template_dir, template_name)
            template_doc = word.Documents.Open(template_path)
            # base_format有且仅有一个
            base_format = self.__get_base_format(base_dir=base_dir, mode="table", base_name=base_name)[0]["format_properties"]
            template_format_list = self.format_reader.read_template_table(template_doc)
            format_list = []

            for i, template_format in enumerate(template_format_list):
                template = template_format.get("format_properties")
                format_dict = self.format_reader.remove_same_property(base_dict=base_format, compared_dict=template)
                format_list.append({"style_name": template_format.get("style_name"),
                                    "table_list":template_format.get("table_list"), "settings": format_dict})

            # print(template_format_list)
            # print(base_format)
            params_path = os.path.join(template_dir, "params.json")
            self.file_tool.write_json_file(format_list, file_path=params_path)
            # print(format_list)
            return format_list

        except Exception as e:
            print(f"Error in template table params get! The details is: {str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_image_params(self, base_dir, template_dir, base_name="base.docx", template_name="template.docx"):
        word = win32.DispatchEx("Word.Application")
        word.Visible = True  # 设为可见（调试时建议开启）
        try:
            # 打开文档
            template_path = os.path.join(template_dir, template_name)
            template_doc = word.Documents.Open(template_path)
            # base_format有且仅有一个
            base_format = self.__get_base_format(base_dir=base_dir, mode="image", base_name=base_name)[0]["format_properties"]
            template_format_list = self.format_reader.read_template_image(template_doc)
            format_list = []

            for i, template_format in enumerate(template_format_list):
                template = template_format.get("format_properties")
                format_dict = self.format_reader.remove_same_property(base_dict=base_format, compared_dict=template)
                format_list.append({"style_name": template_format.get("style_name"),
                                    "image_list":template_format.get("image_list"), "settings": format_dict})

            # print(template_format_list)
            # print(base_format)
            params_path = os.path.join(template_dir, "params.json")
            self.file_tool.write_json_file(format_list, file_path=params_path)
            # print(format_list)
            return format_list

        except Exception as e:
            print(f"Error in template table params get! The details is: {str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def get_template_params(self, base_dir, template_dir, mode = "page",use_catch = True, base_name = "base.docx", template_name = "template.docx"):
        function_dir = {
            "page": self.__init_template_page_params,
            "paragraph": self.__init_template_paragraph_params,
            "table": self.__init_template_table_params,
            "image": self.__init_template_image_params
        }
        params_path = os.path.join(template_dir,"params.json")
        if os.path.exists(params_path) and use_catch:
            return self.file_tool.read_json_file(params_path)
        else:
            get_function = function_dir.get(mode)
            return get_function(base_dir,template_dir,base_name,template_name)

    def __init_template_page_description(self, template_dir,template_name = "template.docx",language='zh'):
        template_doc_path = os.path.join(template_dir,template_name)
        params_path = os.path.join(template_dir, "params.json")
        if not os.path.exists(params_path):
            print("Template Params Not Exist!")
            return None
        params_list = self.file_tool.read_json_file(params_path)
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设为可见（调试时建议开启）
            template_doc = word.Documents.Open(template_doc_path)
            format_description = self.format_reader.read_template_page_description(template_doc, params_list,language=language)
            # print("format_description:",format_description)
            return format_description
        except Exception as e:
            print(f"Error in read template description! The details is: {str(e)}")
            raise
        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_paragraph_description(self, template_dir, template_name = "template.docx",language='zh'):
        template_doc_path = os.path.join(template_dir,template_name)
        params_path = os.path.join(template_dir, "params.json")
        # print(params_path)
        if not os.path.exists(params_path):
            print("Template Params Not Exist!")
            return None
        params_list = self.file_tool.read_json_file(params_path)
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设为可见（调试时建议开启）
            template_doc = word.Documents.Open(template_doc_path)
            format_description = self.format_reader.read_template_paragraph_description(template_doc, params_list,language=language)
            return format_description
        except Exception as e:
            print(f"Error in read template description! The details is: {str(e)}")
            raise
        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_table_description(self, template_dir, template_name = "template.docx",language='zh'):
        template_doc_path = os.path.join(template_dir,template_name)
        params_path = os.path.join(template_dir, "params.json")
        # print(params_path)
        if not os.path.exists(params_path):
            print("Template Params Not Exist!")
            return None
        params_list = self.file_tool.read_json_file(params_path)
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设为可见（调试时建议开启）
            template_doc = word.Documents.Open(template_doc_path)
            format_description = self.format_reader.read_template_table_description(template_doc, params_list,language=language)
            return format_description
        except Exception as e:
            print(f"Error in read template description! The details is: {str(e)}")
            raise
        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def __init_template_image_description(self, template_dir, template_name = "template.docx",language='zh'):
        template_doc_path = os.path.join(template_dir,template_name)
        params_path = os.path.join(template_dir, "params.json")
        # print(params_path)
        if not os.path.exists(params_path):
            print("Template Params Not Exist!")
            return None
        params_list = self.file_tool.read_json_file(params_path)
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设为可见（调试时建议开启）
            template_doc = word.Documents.Open(template_doc_path)
            format_description = self.format_reader.read_template_image_description(template_doc, params_list,language=language)
            return format_description
        except Exception as e:
            print(f"Error in read template description! The details is: {str(e)}")
            raise
        finally:
            # 确保清理资源
            if 'doc' in locals():
                template_doc.Close(SaveChanges=False)
            word.Quit()

    def get_template_description(self,template_dir, mode = "page", template_name = "template.docx" ,language = 'zh',use_catch=True):
        function_dir = {
            "page": self.__init_template_page_description,
            "paragraph": self.__init_template_paragraph_description,
            "table": self.__init_template_table_description,
            "image": self.__init_template_image_description
        }
        descriptions_path = os.path.join(template_dir, "descriptions.json")
        if os.path.exists(descriptions_path) and use_catch:
            return self.file_tool.read_json_file(descriptions_path)
        else:
            get_function = function_dir.get(mode)
            descriptions = get_function(template_dir, template_name=template_name, language=language)
            self.file_tool.write_json_file(data=descriptions,file_path=descriptions_path)
            return descriptions

    def count_subfolders(self, path):
        """统计指定路径下的子文件夹数量"""
        if not os.path.exists(path):
            return 0
        items = os.listdir(path)
        folder_count = 0
        for item in items:
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                folder_count += 1
        return folder_count

    def __page_requirement_generate(self, template_dic, template_page_description, language = 'zh', use_catch=True):
        requirement_save_path = os.path.join(template_dic, "requirement.json")
        if not os.path.exists(requirement_save_path) or not use_catch:
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_config.get("page_requirement_generation").get(language)
                },
                {
                    "role": "user",
                    "content": "user_input: {}".format(str(template_page_description))
                }
            ]
            response = self.llm.generate(messages)
            response = self.file_tool.response_json_parse(response.get("data").get("content"))
            self.file_tool.write_json_file(response, requirement_save_path)
            return response
        else:
            return self.file_tool.read_json_file(requirement_save_path)

    def generate_requirement_page(self,folder_list=[],template_name = "template.docx"):
        # page
        mode = "page"
        languages = [
            'zh',
            'en'
        ]
        base_dir = os.path.join(ABS_DIR, f"data_contribution/template/{mode}")
        for folder in folder_list:
            for language in languages:
                template_num = self.count_subfolders(os.path.join(ABS_DIR, f"data_contribution/template/{mode}/{language}/{folder}"))
                for template_index in range(1,template_num+1):
                    template_dir = os.path.join(ABS_DIR, f"data_contribution/template/{mode}/{language}/{folder}/{mode}{template_index}")
                    template_page_format = self.get_template_params(base_dir, template_dir, mode=mode, use_catch=False)
                    template_page_doc = os.path.join(template_dir,template_name)
                    # self.format_tool.reset_template_page(doc_path=template_page_doc,format_list=template_page_format)
                    template_page_description = self.get_template_description(template_dir, mode=mode, language=language,use_catch=False)
                    page_requirement = self.__page_requirement_generate(template_dir,template_page_description,language=language,use_catch=False)

                # print(template_page_description)

    def __paragraph_requirement_generate(self, template_dic, template_page_description, language = 'zh', use_catch=True):
        requirement_save_path = os.path.join(template_dic, "requirement.json")
        if not os.path.exists(requirement_save_path) or not use_catch:
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_config.get("paragraph_requirement_generation").get(language)
                },
                {
                    "role": "user",
                    "content": "user_input: {}".format(str(template_page_description))
                }
            ]
            response = self.llm.generate(messages)
            response = self.file_tool.response_json_parse(response.get("data").get("content"))
            self.file_tool.write_json_file(response, requirement_save_path)
            return response
        else:
            return self.file_tool.read_json_file(requirement_save_path)

    def generate_requirement_paragraph(self,folder_list=[],template_name = "template.docx"):
        mode = "paragraph"
        languages = [
            'zh',
            'en'
        ]
        base_dir = os.path.join(ABS_DIR, f"data_contribution/template/{mode}")
        for folder in folder_list:
            for language in languages:
                template_num = self.count_subfolders(os.path.join(ABS_DIR, f"data_contribution/template/{mode}/{language}/{folder}"))
                for template_index in range(1,template_num+1):
                    template_dir = os.path.join(ABS_DIR, f"data_contribution/template/{mode}/{language}/{folder}/{mode}{template_index}")
                    template_paragraph_format = self.get_template_params(base_dir, template_dir, mode=mode, use_catch=False)
                    # print(template_paragraph_format)
                    template_paragraph_description = self.get_template_description(template_dir, mode=mode, language=language,use_catch=False)
                    paragraph_requirement = self.__paragraph_requirement_generate(template_dir,template_paragraph_description,language=language,use_catch=False)

                # print(template_page_description)


if __name__ == '__main__':
    try:
        # gencache.EnsureDispatch("Word.Application")
        pass
        folder_list = [
            "poster",

            # "contract", "exam", "gov_doc", "laws", "contract",, "newspaper",
            # "thesis","project","paper","poster", "common","postcard",
        ]
        contribution = DataContribution()
        contribution.generate_requirement_page(folder_list=folder_list)
        # contribution.generate_requirement_paragraph(folder_list=folder_list)

        paragraph_map = ParagraphStyleMap()
        paragraph_map.get_paragraph_style_map(area_list=folder_list)

    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        pass