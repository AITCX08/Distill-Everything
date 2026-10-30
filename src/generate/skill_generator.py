"""
Skill 生成模块

使用 Jinja2 模板将UP主知识画像渲染为 SKILL.md 文件。
"""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from src.model.knowledge_extractor import BloggerProfile

console = Console()


class SkillGenerator:
    """
    SKILL.md 生成器。

    从博主知识画像生成可用于AI助手的Skill文件。
    """

    def __init__(self, template_dir: str = "templates"):
        """
        初始化 Jinja2 模板引擎。

        Args:
            template_dir: 模板文件所在目录
        """
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate(self, profile: BloggerProfile) -> str:
        """
        从博主画像生成 SKILL.md 内容。

        Args:
            profile: 博主知识画像

        Returns:
            渲染后的 SKILL.md 文本内容
        """
        template = self.env.get_template("skill.md.j2")

        content = template.render(
            # 基础身份
            name=profile.name,
            domain=profile.domain,
            self_intro=profile.self_intro,
            signature_quote=profile.signature_quote,
            core_philosophy=profile.core_philosophy,
            # 身份卡
            identity_who=profile.identity_who,
            identity_origin=profile.identity_origin,
            identity_now=profile.identity_now,
            # 心智模型与判断
            mental_models=profile.mental_models,
            decision_heuristics=profile.decision_heuristics,
            # 表达
            style=profile.style,
            signature_phrases=profile.signature_phrases,
            expression_dna=profile.expression_dna,
            # 价值观三层
            values_pursued=profile.values_pursued,
            values_rejected=profile.values_rejected,
            inner_tensions=profile.inner_tensions,
            # 边界
            anti_patterns=profile.anti_patterns,
            honest_boundaries=profile.honest_boundaries,
            knowledge_boundary=profile.knowledge_boundary,
            # 时间线与谱系
            timeline=profile.timeline,
            influenced_by=profile.influenced_by,
            influenced_who=profile.influenced_who,
            # 示例与溯源
            typical_qa_pairs=profile.typical_qa_pairs,
            sources=profile.sources,
            video_sources=profile.video_sources,
            key_quotes=profile.key_quotes,
            research_date=profile.research_date,
            # 旧字段
            core_views=profile.core_views,
            values=profile.values,
            # 元信息
            generation_date=datetime.now().strftime("%Y-%m-%d"),
            video_count=len(profile.sources or profile.video_sources),
        )

        return content

    def save(self, content: str, output_path: Path) -> Path:
        """
        保存生成的 SKILL.md 文件。

        Args:
            content: 渲染后的文本内容
            output_path: 输出文件路径

        Returns:
            保存的文件路径
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        console.print(f"[green]SKILL.md 已生成: {output_path}")
        return output_path

    def generate_and_save(self, profile: BloggerProfile,
                          output_path: Path) -> Path:
        """
        一步完成生成和保存。

        Args:
            profile: 博主知识画像
            output_path: 输出文件路径

        Returns:
            保存的文件路径
        """
        content = self.generate(profile)
        return self.save(content, output_path)
