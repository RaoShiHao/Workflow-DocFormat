from constant import ABS_DIR
import os,shutil,itertools,re
from tools.modify.page_tool import PageTools
from tools.modify.paragraph_tool import TextTools
from tools.reader.file_trans import FileConverter
import win32com.client as win32
from win32com.client import gencache


class DataSetGeneration:
    def __init__(self):
        self.filetool = FileConverter()

    def copy_and_rename_file(self, source_filepath: str, destination_dir: str, new_filename: str):
        """
        将指定的文件复制到目标文件夹，并使用新的名称重命名。

        Args:
            source_filepath (str): 原始文件的完整路径（例如：/path/to/old/document.pdf）。
            destination_dir (str): 目标文件夹的路径（例如：/path/to/new/folder）。
            new_filename (str): 文件复制后的新名称（例如：report_2023.pdf）。

        Returns:
            str: 如果成功，返回新文件的完整路径；如果失败，返回错误信息。
        """

        # 1. 检查源文件是否存在
        if not os.path.exists(source_filepath):
            return f"❌ 错误：源文件不存在：'{source_filepath}'"

        # 2. 确保目标文件夹存在
        # 如果目标文件夹不存在，则创建它（递归创建父目录）
        try:
            os.makedirs(destination_dir, exist_ok=True)
            print(f"✅ 目标文件夹确认/创建成功: '{destination_dir}'")
        except Exception as e:
            return f"❌ 错误：创建目标文件夹失败：{e}"

        # 3. 构建目标文件的完整路径 (复制后带重命名)
        destination_filepath = os.path.join(destination_dir, new_filename)

        # 4. 执行文件复制操作
        try:
            # 使用 shutil.copy2 复制文件，它会尽量保留更多的元数据（如时间戳）
            shutil.copy2(source_filepath, destination_filepath)

            print(f"✅ 文件成功复制并重命名到：'{destination_filepath}'")
            return destination_filepath

        except Exception as e:
            return f"❌ 错误：文件复制失败：{e}"

    import os

    def get_subdirectories(self,directory_path: str) -> list[str]:
        """
        获取指定路径下的所有一级子文件夹（目录）名称。

        Args:
            directory_path (str): 需要搜索的父文件夹路径。

        Returns:
            list[str]: 包含所有子文件夹名称的列表（不包含路径）。
                       如果文件夹不存在或没有子文件夹，则返回空列表。
        """

        subdirectories = []

        # 1. 检查输入的路径是否是有效的目录
        if not os.path.isdir(directory_path):
            print(f"❌ 错误：指定的路径不是一个有效的文件夹或不存在：'{directory_path}'")
            return []

        # print(f"🔍 正在搜索父文件夹：'{directory_path}'")

        # 2. 遍历父文件夹中的所有项目
        try:
            # os.listdir() 返回文件夹内所有文件和文件夹的名称列表
            for item_name in os.listdir(directory_path):
                # 3. 构建项目的完整路径
                item_path = os.path.join(directory_path, item_name)

                # 4. 关键步骤：使用 os.path.isdir() 检查该项目是否是目录（文件夹）
                if os.path.isdir(item_path):
                    # 5. 如果是文件夹，将其名称添加到结果列表
                    subdirectories.append(item_name)

        except Exception as e:
            print(f"⚠️ 搜索过程中发生错误：{e}")
            return []

        if subdirectories:
            # print(f"✅ 找到 {len(subdirectories)} 个子文件夹。")
            pass
        else:
            print("ℹ️ 未找到任何子文件夹。")

        return subdirectories

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

    def create_directory(self, directory_path: str) -> bool:
        """
        创建指定路径的文件夹。如果文件夹不存在，则创建它；如果已存在，则不执行任何操作。

        Args:
            directory_path (str): 需要创建的文件夹的完整路径。

        Returns:
            bool: 如果目录创建成功或已存在，返回 True；如果创建失败，返回 False。
        """

        # 1. 检查路径是否为空
        if not directory_path:
            print("❌ 错误：提供的目录路径不能为空。")
            return False
        print(f"尝试创建目录: '{directory_path}'")
        try:
            # os.makedirs 是核心函数。
            # 它会递归地创建所有不存在的父目录。
            # exist_ok=True 是关键：如果目录已经存在，不会抛出 FileExistsError 错误。
            os.makedirs(directory_path, exist_ok=True)
            # 2. 验证目录是否成功存在
            if os.path.isdir(directory_path):
                print(f"✅ 目录创建成功或已存在: '{directory_path}'")
                return True
            else:
                # 这通常只在权限问题或特殊文件系统配置下发生
                print(f"⚠️ 警告：目录创建后验证失败: '{directory_path}'")
                return False
        except PermissionError:
            print(f"❌ 错误：没有权限创建目录: '{directory_path}'")
            return False
        except Exception as e:
            print(f"❌ 发生未知错误，创建目录失败: {e}")
            return False

    def copy_all_files(self, source_dir: str, target_dir: str):
        """
        将原始目录(source_dir)中的所有文件和子目录复制到目标目录(target_dir)中。

        Args:
            source_dir (str): 原始目录路径
            target_dir (str): 目标目录路径
        """
        # 1. 检查源目录是否存在
        if not os.path.exists(source_dir):
            print(f"❌ 错误：原始目录不存在 -> {source_dir}")
            return

        # 2. 如果目标目录不存在，则创建它
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
            # print(f"📁 已创建目标目录: {target_dir}")

        # 3. 遍历源目录中的所有内容
        # os.listdir 获取当前层级的所有文件名和文件夹名
        for item_name in os.listdir(source_dir):
            # 构建完整的源路径和目标路径
            source_path = os.path.join(source_dir, item_name)
            target_path = os.path.join(target_dir, item_name)

            try:
                # 4. 判断是文件还是目录
                if os.path.isdir(source_path):
                    # 如果是目录，使用 copytree 递归复制整个子目录
                    # dirs_exist_ok=True 允许目标子目录已存在而不报错 (Python 3.8+)
                    shutil.copytree(source_path, target_path, dirs_exist_ok=True)
                    # print(f"🌿 已复制子目录: {item_name}")
                else:
                    # 如果是文件，使用 copy2 (保留元数据，如修改时间)
                    shutil.copy2(source_path, target_path)
                    # print(f"📄 已复制文件: {item_name}")

            except Exception as e:
                print(f"⚠️ 复制 {item_name} 时出错: {e}")

        # print(f"\n✨ 复制完成！所有内容已从 '{source_dir}' 迁移至 '{target_dir}'")

    def compose_content_template(self,folder_list=[]):
        count = 0
        for language in ["en", "zh"]:
            language_count = 0
            for area in folder_list:
                paragraph_list = self.get_subdirectories(os.path.join(ABS_DIR,f"data_contribution/template/paragraph/{language}/{area}"))
                page_list = self.get_subdirectories(os.path.join(ABS_DIR,f"data_contribution/template/page/{language}/{area}"))
                content_list = self.filter_docx_files(os.path.join(ABS_DIR,f"data_contribution/template/content/{language}/{area}"))
                data_compose_list = list(itertools.product(paragraph_list, page_list, content_list))
                for data_compose in data_compose_list:
                    paragraph_num, page_num, content_num = data_compose[0],data_compose[1],data_compose[2]
                    data_dir = os.path.join(ABS_DIR,f"data_contribution/datasets/{language}/{area}/{content_num}_{paragraph_num}_{page_num}")

                    self.create_directory(data_dir)

                    template_page_dir = os.path.join(ABS_DIR,f"data_contribution/template/page/{language}/{area}/{page_num}")
                    template_paragraph_dir = os.path.join(ABS_DIR, f"data_contribution/template/paragraph/{language}/{area}/{paragraph_num}")
                    template_content_path = os.path.join(ABS_DIR,f"data_contribution/template/content/{language}/{area}/{content_num}")
                    template_map_path = os.path.join(ABS_DIR, f"data_contribution/template/content/{language}/{area}/{content_num}.json")
                    # copy page
                    self.copy_all_files(source_dir=template_page_dir, target_dir=os.path.join(data_dir,"meta/page"))

                    # copy paragraph
                    self.copy_all_files(source_dir=template_paragraph_dir, target_dir=os.path.join(data_dir,"meta/paragraph"))

                    # copy content
                    self.copy_and_rename_file(source_filepath=template_content_path,destination_dir=data_dir,new_filename="source_content.docx")

                    # copy paragraph_map
                    self.copy_and_rename_file(source_filepath=template_map_path,destination_dir=data_dir,new_filename="paragraph_style_map.json")


                    # print(data_dir)
                    count+=1
                    language_count +=1

            print(f"{language}: ", language_count)
        print(count)

    def __extract_paragraph_style(self,text):
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
            style_list.append({"index":para_index,"style_name":style})
        style_map = self.__convert_map(style_list)
        return style_map

    def __get_docx_map(self,doc_path):
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

    def get_paragraph_style_map(self,area_list=[]):
        data_dir = os.path.join(ABS_DIR, "data_contribution/datasets")
        languages = [
            'zh',
            'en'
        ]
        for language in languages:
            for area in area_list:
                area_dir = os.path.join(data_dir,f"{language}/{area}")
                template_list = self.get_subdirectories(area_dir)
                for template in template_list:
                    template_dir = os.path.join(area_dir,template)
                    template_content_path = os.path.join(template_dir,"modified_content.docx")
                    paragraphs_map = self.__get_docx_map(template_content_path)
                    self.filetool.write_json_file(paragraphs_map,os.path.join(template_dir,"paragraph_style_map.json"))

class FormatGeneration:
    def __init__(self):
        self.page_tool = PageTools()
        self.text_tool = TextTools()
        self.file_tool = FileConverter()

    def copy_and_rename_file(self, source_filepath: str, destination_dir: str, new_filename: str):
        # 1. 检查源文件是否存在
        if not os.path.exists(source_filepath):
            return f"❌ 错误：源文件不存在：'{source_filepath}'"
        # 2. 确保目标文件夹存在
        # 如果目标文件夹不存在，则创建它（递归创建父目录）
        try:
            os.makedirs(destination_dir, exist_ok=True)
            print(f"✅ 目标文件夹确认/创建成功: '{destination_dir}'")
        except Exception as e:
            return f"❌ 错误：创建目标文件夹失败：{e}"
        # 3. 构建目标文件的完整路径 (复制后带重命名)
        destination_filepath = os.path.join(destination_dir, new_filename)
        # 4. 执行文件复制操作
        try:
            # 使用 shutil.copy2 复制文件，它会尽量保留更多的元数据（如时间戳）
            shutil.copy2(source_filepath, destination_filepath)
            print(f"✅ 文件成功复制并重命名到：'{destination_filepath}'")
            return destination_filepath
        except Exception as e:
            return f"❌ 错误：文件复制失败：{e}"
    def get_subdirectories(self,directory_path: str) -> list[str]:
        subdirectories = []
        # 1. 检查输入的路径是否是有效的目录
        if not os.path.isdir(directory_path):
            print(f"❌ 错误：指定的路径不是一个有效的文件夹或不存在：'{directory_path}'")
            return []
        # 2. 遍历父文件夹中的所有项目
        try:
            # os.listdir() 返回文件夹内所有文件和文件夹的名称列表
            for item_name in os.listdir(directory_path):
                # 3. 构建项目的完整路径
                item_path = os.path.join(directory_path, item_name)

                # 4. 关键步骤：使用 os.path.isdir() 检查该项目是否是目录（文件夹）
                if os.path.isdir(item_path):
                    # 5. 如果是文件夹，将其名称添加到结果列表
                    subdirectories.append(item_name)

        except Exception as e:
            print(f"⚠️ 搜索过程中发生错误：{e}")
            return []

        if subdirectories:
            # print(f"✅ 找到 {len(subdirectories)} 个子文件夹。")
            pass
        else:
            print("ℹ️ 未找到任何子文件夹。")

        return subdirectories
    def __page_generate(self,doc, style_list):
        # print(doc.Sections.Count)
        for style_dict in style_list:
            settings = style_dict.get("settings", None)
            location_list = style_dict.get("section_list")
            if settings is not None:
                self.page_tool.set_format(doc=doc, location_list=location_list, settings=settings)

    def __convert_format_list_to_dict(self, format_list):
        format_dict = {}
        for format in format_list:
            style_name = format.get("style_name")
            settings = format.get("settings")
            format_dict[style_name] = settings
        return format_dict
    def __paragraph_generate(self,doc,style_dict, style_map):
        for style_name, location_list in style_map.items():
            settings = style_dict.get(style_name, None)
            if settings is not None:
                self.text_tool.set_format(doc=doc,location_list=location_list,settings=settings)

    def __init_template(self,docx_path, text_style_dict, text_style_map,page_style_list):
        try:
            # 连接Word
            word = win32.DispatchEx("Word.Application")  # 或使用Dispatch
            # print(word.ActivePrinter)
            word.ActivePrinter = "Microsoft Print to PDF"
            word.Visible = True  # 设为不可见（调试时建议开启）
            doc = word.Documents.Open(os.path.join(ABS_DIR, docx_path))
            self.__page_generate(doc,page_style_list)
            self.__paragraph_generate(doc, style_dict=text_style_dict,style_map=text_style_map)

        except Exception as e:
            print(f"操作失败：{str(e)}")
            raise

        finally:
            # 确保清理资源
            if 'doc' in locals():
                doc.Close(SaveChanges=False)
            word.Quit()

    def template_format_generate(self, area_list = []):
        data_dir = os.path.join(ABS_DIR, "data_contribution/datasets")
        languages = [
            'zh',
            'en'
        ]
        for language in languages:
            for area in area_list:
                area_dir = os.path.join(data_dir,f"{language}/{area}")
                template_list = self.get_subdirectories(area_dir)
                for template in template_list:
                    template_dir = os.path.join(area_dir,template)

                    # 实际修改modified_content.docx
                    template_content_path = os.path.join(template_dir,"source_content.docx")
                    self.copy_and_rename_file(source_filepath=template_content_path, destination_dir=template_dir,new_filename="modified_content.docx")
                    template_content_path = os.path.join(template_dir, "modified_content.docx")

                    # 获取相应的格式信息
                    page_params_path = os.path.join(template_dir,"meta/page/params.json")
                    page_params = self.file_tool.read_json_file(page_params_path)
                    paragraph_params_path = os.path.join(template_dir,"meta/paragraph/params.json")
                    paragraph_params_list = self.file_tool.read_json_file(paragraph_params_path)
                    paragraph_params_dict = self.__convert_format_list_to_dict(paragraph_params_list)
                    paragraph_map_path = os.path.join(template_dir,"paragraph_style_map.json")
                    paragraph_map = self.file_tool.read_json_file(paragraph_map_path)
                    self.__init_template(docx_path=template_content_path,text_style_dict=paragraph_params_dict,
                                         text_style_map=paragraph_map,page_style_list=page_params)
                    print(f"{template_content_path} modified.")

if __name__ == '__main__':
    try:
        # gencache.EnsureDispatch("Word.Application")
        folder_list = [
            # "thesis","project","paper"
            "newspaper"
            # "contract", "exam", "gov_doc", "laws", "contract", "newspaper","letter",
            # "thesis","project","paper","poster", "common","postcard",
        ]
        # 复制模板组织内容

        datacontribution = DataSetGeneration()
        # 复制content并组合！
        datacontribution.compose_content_template(folder_list)

        # 获取对应的content_style_map
        # datacontribution.get_paragraph_style_map(folder_list)

        # 内容的格式调整初始化

        format_gen = FormatGeneration()
        format_gen.template_format_generate(folder_list)


    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        pass