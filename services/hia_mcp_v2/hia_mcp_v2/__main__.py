from .stdio import serve_from_environment


def main() -> int:
    return serve_from_environment()


if __name__ == "__main__":
    raise SystemExit(main())
