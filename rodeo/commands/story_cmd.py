"""rodeo story — workshop narrative in different languages and variants.

Group so A3 can add `story new` / `story rewrite` (LLM generation) later.
`story check` compares a content repository's language branches with its source
branch (see rodeo/langbranch.py).
"""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from ..config import load_config
from ..story import render_story
from ._options import config_options

# Status goes to stderr so stdout stays pipeable markdown.
console = Console(stderr=True)


@click.group("story")
def story_cmd() -> None:
    """Render the lab's workshop story (see <lab>/story/ and the plan's story: block)."""


@story_cmd.command("render")
@config_options
@click.option("--language", default=None, metavar="LANG",
              help="Target language (BCP 47, e.g. 'es'); default: plan story.language or 'en'.")
@click.option("--story-id", default=None, metavar="ID",
              help="Story variant to assemble (story/stories/<ID>.yaml); default: plan story.id or all spans.")
@click.option("--engine", default=None, metavar="NAME",
              help="rmstory translation engine to fill missing translations (e.g. gemini, ollama).")
@click.option("-o", "--output", default="-", metavar="FILE", show_default="stdout",
              help="Write the rendered hand-out to FILE.")
def story_render_cmd(
    config_path: str,
    config_dir: str | None,
    params: tuple[str, ...],
    paramfile: str | None,
    language: str | None,
    story_id: str | None,
    engine: str | None,
    output: str,
) -> None:
    """Render the workshop hand-out for this lab's topology, language, and story.

    \b
    Examples:
      rodeo story render                          # source language, all spans
      rodeo story render --language es -o es.md   # Spanish hand-out
      rodeo story render --story-id villain-arc --language fr
    """
    if config_dir is None:
        ctx = click.get_current_context()
        if ctx.obj:
            config_dir = ctx.obj.get("config_dir")

    cfg = load_config(config_path, params=params, paramfile=paramfile, config_dir=config_dir)
    text = render_story(cfg, language=language, story_id=story_id, engine=engine)

    if output == "-":
        click.echo(text, nl=not text.endswith("\n"))
    else:
        Path(output).write_text(text)
        console.print(f"[green]✓  Wrote {output}[/green]")


@story_cmd.command("check")
@click.argument("repo", default=".", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--language", "languages", multiple=True, metavar="LANG",
              help="Language branch to check (repeatable); default: every branch named like a "
                   "two-letter language tag (es, pt-br).")
@click.option("--source", default=None, metavar="REF",
              help="The source-language branch; default: the clone's default branch.")
def story_check_cmd(repo: Path, languages: tuple[str, ...], source: str | None) -> None:
    """Say which language branches of the content repository REPO (a git clone, default: here)
    are behind its source branch, and the commits they miss.

    Changes under rmstory/, rmstory-stories/, story/ and .github/ don't count. Exits 1
    when a branch is behind or can't be checked. In CI, check out the full history
    (e.g. fetch-depth: 0).
    """
    from .. import langbranch

    try:
        source = source or langbranch.default_branch(repo)
        found = langbranch.language_branches(repo, source)
    except RuntimeError as exc:
        click.echo(f"✗  {repo}: {exc}")
        raise SystemExit(1)
    branches = {lang: found.get(lang, lang) for lang in languages} if languages else found
    if not branches:
        click.echo(f"No language branches next to {source}.")
        return
    failed = 0
    for lang, ref in branches.items():
        try:
            commits = langbranch.behind(repo, ref, source)
        except RuntimeError as exc:
            failed += 1
            click.echo(f"✗  {lang}: {exc}")
            continue
        if not commits:
            click.echo(f"✓  {lang} is up to date with {source}")
            continue
        failed += 1
        click.echo(f"✗  {lang} is {len(commits)} commit(s) behind {source}:")
        for commit in commits:
            click.echo(f"     {commit}")
    if failed:
        raise SystemExit(1)
