"""Editable HOM builder used by the real-time Houdini MCP path."""

from __future__ import annotations

from typing import Any


def build(parent_path: str = "/obj", root_name: str = "HIA_Result") -> str:
    """Build an editable vending machine network in the current HIP.

    The function never clears, loads, or saves a HIP. Every created SOP is
    contained by one newly-created geometry root and the returned value is the
    actual unique root path.
    """

    if not isinstance(parent_path, str) or not parent_path.startswith("/"):
        raise ValueError("parent_path must be an absolute Houdini node path")
    if (
        not isinstance(root_name, str)
        or not root_name.strip()
        or "/" in root_name
        or len(root_name) > 128
    ):
        raise ValueError("root_name must be a non-empty Houdini node name")

    import hou

    parent = hou.node(parent_path)
    if parent is None:
        raise ValueError(f"Houdini parent does not exist: {parent_path}")

    with hou.undos.group("HIA: Build Vending Machine"):
        root = parent.createNode(
            "geo",
            node_name=root_name,
            run_init_scripts=False,
            force_valid_node_name=True,
        )

        outputs: list[Any] = []

        def add_box(
            name: str,
            size: tuple[float, float, float],
            center: tuple[float, float, float],
            color: tuple[float, float, float],
        ) -> None:
            box = root.createNode("box", node_name=name)
            box.parmTuple("size").set(size)
            box.parmTuple("t").set(center)
            color_node = root.createNode("color", node_name=f"{name}_color")
            color_node.setInput(0, box)
            color_node.parmTuple("color").set(color)
            outputs.append(color_node)

        # Cabinet and front glazing.
        add_box("cabinet", (2.6, 1.35, 4.2), (0.0, 0.0, 2.1), (0.16, 0.19, 0.23))
        add_box("display_glass", (1.75, 0.12, 2.15), (-0.28, -0.74, 2.65), (0.08, 0.20, 0.28))
        add_box("control_panel", (0.42, 0.14, 1.45), (0.91, -0.75, 2.55), (0.72, 0.74, 0.76))
        add_box("payment_screen", (0.27, 0.08, 0.30), (0.91, -0.86, 2.93), (0.12, 0.42, 0.60))
        add_box("coin_slot", (0.22, 0.08, 0.07), (0.91, -0.86, 2.58), (0.05, 0.05, 0.05))
        add_box("keypad", (0.25, 0.08, 0.35), (0.91, -0.86, 2.22), (0.12, 0.12, 0.12))
        add_box("dispense_door", (1.35, 0.16, 0.48), (-0.25, -0.77, 0.58), (0.04, 0.05, 0.06))

        # Product cans arranged as two editable rows.
        product_colors = (
            (0.90, 0.16, 0.12),
            (0.95, 0.63, 0.10),
            (0.15, 0.58, 0.28),
            (0.12, 0.42, 0.82),
            (0.63, 0.24, 0.76),
            (0.92, 0.35, 0.55),
        )
        for index, color in enumerate(product_colors):
            row, column = divmod(index, 3)
            add_box(
                f"product_{index + 1:02d}",
                (0.38, 0.30, 0.62),
                (-0.78 + column * 0.52, -0.83, 3.18 - row * 0.82),
                color,
            )

        add_box("left_foot", (0.42, 0.90, 0.18), (-0.82, 0.0, 0.09), (0.05, 0.05, 0.06))
        add_box("right_foot", (0.42, 0.90, 0.18), (0.82, 0.0, 0.09), (0.05, 0.05, 0.06))

        merge = root.createNode("merge", node_name="assemble_vending_machine")
        for output in outputs:
            merge.setNextInput(output)
        final = root.createNode("null", node_name="OUT_VENDING_MACHINE")
        final.setInput(0, merge)
        final.setDisplayFlag(True)
        final.setRenderFlag(True)
        root.layoutChildren()
        root.setDisplayFlag(True)
        root.setSelected(True, clear_all_selected=True)
        return root.path()


__all__ = ["build"]
