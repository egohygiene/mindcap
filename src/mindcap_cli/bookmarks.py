"""Thin presentation adapter for the local Chrome bookmark review workflow."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mindcap import MindcapApplication
from mindcap.contracts import (
    BookmarkBatchesCommand,
    BookmarkBatchesResult,
    PrepareBookmarksCommand,
)
from mindcap.core.errors import MindcapError

app = typer.Typer(
    no_args_is_help=True,
    help="Capture local Chrome bookmarks and prepare files for manual review.",
)
console = Console()
errors = Console(stderr=True)


class ChromeChannel(StrEnum):
    stable = "stable"
    beta = "beta"
    dev = "dev"
    canary = "canary"


def _render(result: BookmarkBatchesResult, *, json_output: bool, quiet: bool) -> None:
    if json_output:
        console.print_json(data=result.model_dump(mode="json"))
        return
    if quiet:
        console.print(
            str(result.first_batch or result.output_directory),
            markup=False,
            soft_wrap=True,
        )
        return
    table = Table("Bookmark review", "Count")
    for label, value in (
        ("Captured readable profiles", result.profile_count),
        ("Captured bookmark occurrences", result.bookmark_count),
        ("Included occurrences (duplicates retained)", result.included_count),
        ("Withheld for local inspection", result.withheld_count),
        ("Numbered review batches", result.batch_count),
    ):
        table.add_row(label, str(value))
    console.print(table)
    if result.capture_warning_count:
        errors.print(
            f"Capture recorded {result.capture_warning_count} warning(s); "
            "inspect the private archive locally for skipped or unusual records."
        )
    console.print("[bold]Private original capture (keep local):[/bold]")
    console.print(str(result.source_bundle), markup=False, soft_wrap=True)
    console.print("[bold]Review folder and progress checklist:[/bold]")
    console.print(str(result.output_directory), markup=False, soft_wrap=True)
    if result.first_batch:
        console.print("[bold]First file to inspect, then attach here:[/bold]")
        console.print(str(result.first_batch), markup=False, soft_wrap=True)
        console.print(
            "Read the file before sharing: titles and URLs can still be personal. "
            "Attach one numbered batch at a time; mark progress in its README."
        )
    else:
        console.print(
            "No eligible links were available for a review batch. "
            "Check the local review folder's counts and withheld record references."
        )


def _fail(error: Exception) -> None:
    errors.print("Bookmark preparation failed:", style="bold red")
    errors.print(str(error), markup=False, soft_wrap=True)
    raise typer.Exit(code=1) from error


@app.command()
def prepare(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Private workspace; defaults to ~/Downloads/mindcap-bookmarks.",
        ),
    ] = None,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", min=1, max=200, help="Maximum links per file."),
    ] = 50,
    user_data_dir: Annotated[
        list[Path] | None,
        typer.Option(
            "--user-data-dir", help="Chrome user-data root; repeat if needed."
        ),
    ] = None,
    profile: Annotated[
        list[str] | None,
        typer.Option(
            "--profile", help="Profile folder name; default all readable profiles."
        ),
    ] = None,
    channel: Annotated[
        ChromeChannel, typer.Option("--channel", help="Chrome channel to discover.")
    ] = ChromeChannel.stable,
    json_output: Annotated[
        bool, typer.Option("--json", help="Return counts and local paths as JSON.")
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Print only the first batch or review folder."),
    ] = False,
) -> None:
    """Capture readable Chrome profiles, verify, and write numbered Markdown batches."""
    try:
        result = MindcapApplication.default().prepare_bookmarks(
            PrepareBookmarksCommand(
                output_root=output,
                batch_size=batch_size,
                user_data_dirs=list(user_data_dir or []),
                profiles=list(profile or []),
                channel=channel.value,
            )
        )
        _render(result, json_output=json_output, quiet=quiet)
    except (MindcapError, OSError, ValueError) as error:
        _fail(error)


@app.command()
def batches(
    bundle: Annotated[
        Path, typer.Argument(help="Existing Chrome capture version folder.")
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="New review directory; existing files stay intact."
        ),
    ] = None,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", min=1, max=200, help="Maximum links per file."),
    ] = 50,
    json_output: Annotated[
        bool, typer.Option("--json", help="Return counts and local paths as JSON.")
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Print only the first batch or review folder."),
    ] = False,
) -> None:
    """Make another batch set from a verified capture, without reading Chrome again."""
    try:
        result = MindcapApplication.default().bookmark_batches(
            BookmarkBatchesCommand(
                bundle_path=bundle, output_directory=output, batch_size=batch_size
            )
        )
        _render(result, json_output=json_output, quiet=quiet)
    except (MindcapError, OSError, ValueError) as error:
        _fail(error)
