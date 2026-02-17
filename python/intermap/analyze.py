"""Command dispatcher for intermap analysis.

Routes --command values to the appropriate analysis function.
Called from __main__.py.
"""

from __future__ import annotations


def dispatch(command: str, project: str, args: dict) -> dict:
    """Dispatch a command to the appropriate analysis function.

    Args:
        command: Analysis command name
        project: Project root path
        args: Extra arguments dict

    Returns:
        Dict result from the analysis function
    """
    if command == "structure":
        from .code_structure import get_code_structure
        return get_code_structure(
            project,
            language=args.get("language", "python"),
            max_results=args.get("max_results", 1000),
        )

    elif command == "impact":
        from .analysis import analyze_impact
        return analyze_impact(
            project,
            target_func=args.get("target", ""),
            max_depth=args.get("max_depth", 3),
            target_file=args.get("target_file"),
            language=args.get("language", "python"),
        )

    elif command == "dead_code":
        from .analysis import analyze_dead_code
        return analyze_dead_code(
            project,
            entry_points=args.get("entry_points"),
            language=args.get("language", "python"),
        )

    elif command == "architecture":
        from .analysis import analyze_architecture
        return analyze_architecture(
            project,
            language=args.get("language", "python"),
        )

    elif command == "change_impact":
        from .change_impact import analyze_change_impact
        return analyze_change_impact(
            project,
            files=args.get("files"),
            use_session=args.get("use_session", False),
            use_git=args.get("use_git", False),
            git_base=args.get("git_base", "HEAD~1"),
            language=args.get("language", "python"),
            max_depth=args.get("max_depth", 5),
        )

    elif command == "diagnostics":
        from .diagnostics import get_project_diagnostics
        return get_project_diagnostics(
            project,
            language=args.get("language", "python"),
        )

    elif command == "call_graph":
        from .cross_file_calls import build_project_call_graph
        graph = build_project_call_graph(
            project,
            language=args.get("language", "python"),
        )
        return {
            "edges": [list(e) for e in graph.edges],
            "edge_count": len(graph.edges),
        }

    elif command == "extract":
        from .extractors import DefaultExtractor
        extractor = DefaultExtractor()
        result = extractor.extract(args.get("file", project))
        return result.to_dict()

    else:
        return {"error": "UnknownCommand", "message": f"Unknown command: {command}"}
