from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union


@dataclass
class FileClassification:
    role: str = "application"
    surface: str = "runtime"
    label: str = ""
    isRuntimeSource: bool = False
    isRuntimeEntryCandidate: bool = False
    isRuntimeHotspotCandidate: bool = False
    isBuildTooling: bool = False
    isGenerator: bool = False
    isTest: bool = False
    isTestRunner: bool = False
    isFixture: bool = False
    isDocumentation: bool = False
    isSpecification: bool = False
    isVendor: bool = False
    isGenerated: bool = False
    isGeneratedSdk: bool = False
    isLocalization: bool = False
    isDependencyLock: bool = False
    isConfig: bool = False
    isEnvironmentSetup: bool = False
    manualEditPolicy: str = ""
    largeFilePolicy: str = ""
    isLockfile: bool = False

    def __post_init__(self):
        if self.isDependencyLock:
            self.isLockfile = True


MONOREPO_ROOTS = {"packages", "apps", "services", "crates", "modules", "libs"}

LOCALE_STEMS = {
    "en", "fr", "de", "es", "it", "pt", "ru", "ja", "zh", "ko", "ar", "hi",
    "nl", "pl", "tr", "sv", "da", "fi", "nb", "cs", "hu", "ro", "uk", "el",
    "he", "th", "vi", "id", "ms", "tl", "bn", "ta", "te", "mr", "gu", "kn",
    "ml", "zht",
}

RUNTIME_HOTSPOT_NAMES = {
    "provider", "providers", "models", "model",
    "parsing", "parser", "cache", "caching",
    "session", "sessions", "middleware",
    "registry", "store", "storage",
    "database", "db", "connection",
    "service", "services",
    "handler", "handlers",
    "config", "configuration",
    "logging", "logger",
    "metrics", "telemetry",
    "plugin", "plugins",
    "extension", "extensions",
}

LOCKFILE_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "cargo.lock", "go.sum", "poetry.lock", "gemfile.lock",
    "composer.lock", "mix.lock", "pipfile.lock",
}

ARCHETYPE_APP = "app"
ARCHETYPE_CLI_SERVER = "cli_server"
ARCHETYPE_DESKTOP_APP = "desktop_app"
ARCHETYPE_BROWSER_ENGINE = "browser_engine"
ARCHETYPE_FRAMEWORK_LIBRARY = "framework_library"
ARCHETYPE_MONOREPO = "monorepo"
ARCHETYPE_TEST_SUITE = "test_suite"
ARCHETYPE_VENDOR_HEAVY = "vendor_heavy"
ARCHETYPE_GENERATED_HEAVY = "generated_heavy"
ARCHETYPE_DOCUMENTATION_HEAVY = "documentation_heavy"
ARCHETYPE_MIXED_LANGUAGE = "mixed_language"


def _path_stem(path: str) -> str:
    return Path(path).stem.lower()


def _path_name(path: str) -> str:
    return Path(path).name


def _path_name_lower(path: str) -> str:
    return Path(path).name.lower()


def _path_parts(path: str) -> tuple[str, ...]:
    return Path(path).parts


def _path_parts_lower(path: str) -> tuple[str, ...]:
    return tuple(p.lower() for p in Path(path).parts)


def _lower_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def _ext(info: Optional[Dict[str, Any]], path: str) -> str:
    if info:
        ext = info.get("extension")
        if ext:
            return str(ext).lower()
    return Path(path).suffix.lower()


def riskFromScore(score: int) -> str:
    if score >= 85:
        return "low"
    elif score >= 65:
        return "medium"
    return "high"


def detectRepoArchetype(
    files: Dict[str, Dict[str, Any]],
    components: list[Dict[str, Any]],
    primary_language: str,
    frameworks: list[str],
) -> Dict[str, Any]:
    component_paths = {c["path"].lower() for c in components}
    all_paths_lower = {p.lower() for p in files}

    has_cmd = any(p.startswith("cmd/") for p in files)
    has_api = any(p.startswith("api/") for p in files)
    has_server = any(p.startswith("server/") for p in files)
    has_src = any(p.startswith("src/") for p in files)

    has_packages = any(p.startswith("packages/") for p in files)
    has_apps = any(p.startswith("apps/") for p in files)
    has_crates = any(p.startswith("crates/") for p in files)
    has_services = any(p.startswith("services/") for p in files)
    has_modules = any(p.startswith("modules/") for p in files)

    has_vendor = any(
        p.startswith("vendor/") or p.startswith("third_party/") or p.startswith("external/") or p.startswith("3rdparty/")
        for p in files
    )
    has_gen = any("/gen/" in p or "/generated/" in p for p in files)
    has_docs = any(p.startswith("docs/") for p in files)

    has_main_rs = any(p.endswith("main.rs") for p in files)
    has_main_go = any(p.endswith("main.go") for p in files)
    has_main_ts = any(p.endswith("main.ts") for p in files)
    has_main_cpp = any(p.endswith("main.cpp") or p.endswith("main.cc") for p in files)
    
    has_main_in_app_root = any(
        p.endswith("main.rs") or p.endswith("main.go") or p.endswith("main.ts") or p.endswith("main.py") or p.endswith("main.cpp") or p.endswith("main.cc")
        for p in files
        if not ("/gen/" in p or "/generated/" in p or "/genop/" in p or "/tools/" in p or p.startswith("tools/") or "/cmd/" in p or p.startswith("cmd/"))
    )

    tauri_main = any("src-tauri/src/main.rs" in p for p in files)

    has_browser_signals = (
        "libraries/libweb" in component_paths
        or "libraries/libjs" in component_paths
        or "ak" in component_paths
        or any("libraries/lib" in p for p in files)
    )

    is_go_project = primary_language == "go" and any(p.endswith("go.mod") for p in files)

    secondary = []
    primary = ARCHETYPE_APP
    confidence = "medium"
    workflow = "app"

    if has_browser_signals:
        primary = ARCHETYPE_BROWSER_ENGINE
        confidence = "high"
        workflow = "browser_engine"

    if tauri_main:
        primary = ARCHETYPE_DESKTOP_APP
        confidence = "high"
        workflow = "desktop_app"

    if (has_packages and len([p for p in files if p.startswith("packages/")]) > 5) or (has_crates and len([p for p in files if p.startswith("crates/")]) > 5) or (has_apps and has_services) or (has_modules and has_services):
        primary = ARCHETYPE_MONOREPO
        confidence = "high"
        workflow = "monorepo"

    if (has_main_in_app_root or tauri_main) and (has_cmd or has_api or has_server or has_src):
        if is_go_project:
            primary = ARCHETYPE_CLI_SERVER
            confidence = "high"
            workflow = "cli_server"
        elif tauri_main:
            primary = ARCHETYPE_DESKTOP_APP
            confidence = "high"
            workflow = "desktop_app"
        else:
            primary = ARCHETYPE_APP
            confidence = "high"
            workflow = "app"

    if is_go_project and not has_main_go:
        primary = ARCHETYPE_FRAMEWORK_LIBRARY
        confidence = "medium"
        workflow = "framework_library"

    if has_vendor and len([p for p in files if p.startswith("vendor/") or p.startswith("third_party/")]) > 20:
        secondary.append(ARCHETYPE_VENDOR_HEAVY)

    if has_gen and len([p for p in files if "/gen/" in p or "/generated/" in p]) > 20:
        secondary.append(ARCHETYPE_GENERATED_HEAVY)

    docs_count = len([p for p in files if p.startswith("docs/") or p.endswith(".md")])
    source_count = len([p for p in files if Path(p).suffix in {".py", ".js", ".ts", ".rs", ".go", ".cpp", ".c", ".h", ".java", ".cc", ".hpp", ".hh", ".hxx"}])
    if docs_count > 0 and docs_count > source_count * 2 and source_count > 0:
        secondary.append(ARCHETYPE_DOCUMENTATION_HEAVY)

    test_count = len([p for p in files if "/test/" in p or "/tests/" in p or p.endswith("_test.go")])
    if test_count > 0 and test_count > source_count * 2:
        secondary.append(ARCHETYPE_TEST_SUITE)

    languages = set()
    for p in files:
        ext = Path(p).suffix.lower()
        if ext in {".py", ".js", ".ts", ".rs", ".go", ".cpp", ".c", ".h", ".hpp", ".cc", ".java", ".kt", ".rb", ".cs"}:
            languages.add(ext)
    if len(languages) >= 3:
        secondary.append(ARCHETYPE_MIXED_LANGUAGE)

    # Framework/library heuristics: large API/runtime directories and no dominant app entry point
    has_large_runtime = any(
        "/core/" in p or "/runtime/" in p or "/api/" in p or "/python/" in p or "/compiler/" in p or "/lite/" in p
        or p.startswith("core/") or p.startswith("runtime/") or p.startswith("api/") or p.startswith("python/") or p.startswith("compiler/") or p.startswith("lite/")
        for p in files
    )
    main_count = sum(1 for p in files if p.endswith("main.rs") or p.endswith("main.go") or p.endswith("main.py") or p.endswith("main.cpp") or p.endswith("main.cc") or p.endswith("main.ts"))
    main_in_gen_count = sum(
        1 for p in files
        if (p.endswith("main.rs") or p.endswith("main.go") or p.endswith("main.py") or p.endswith("main.cpp") or p.endswith("main.cc") or p.endswith("main.ts"))
        and ("/gen/" in p or "/generated/" in p or "/genop/" in p or "/tools/" in p or p.startswith("tools/") or "/cmd/" in p or p.startswith("cmd/"))
    )
    effective_main_count = main_count - main_in_gen_count
    if has_large_runtime and effective_main_count <= 2 and source_count > 3 and primary == ARCHETYPE_APP:
        primary = ARCHETYPE_FRAMEWORK_LIBRARY
        confidence = "medium"
        workflow = "framework_library"

    if not has_main_in_app_root and (has_api or has_src or has_large_runtime) and primary == ARCHETYPE_APP:
        primary = ARCHETYPE_FRAMEWORK_LIBRARY
        confidence = "medium"
        workflow = "framework_library"

    if primary == ARCHETYPE_APP and not has_browser_signals and not tauri_main:
        workflow = "app"

    return {
        "primaryArchetype": primary,
        "secondaryArchetypes": secondary,
        "confidence": confidence,
        "workflowStrategy": workflow,
    }


def _is_runtime_entry_name(name: str) -> bool:
    return name in {"main.rs", "main.go", "main.ts", "main.py", "main.cpp", "main.cc", "index.ts", "index.js", "app.ts", "app.js", "server.ts", "server.js", "server.py", "app.py", "cli.py", "cli.ts", "cmd.ts", "cmd.py", "bootstrap.py", "bootstrap.ts", "main.py", "main.java", "Main.java", "Program.cs"}


def _is_gen_name(name: str) -> bool:
    stem = Path(name).stem.lower()
    if name.endswith("_gen.go") or name.endswith("_generated.go"):
        return True
    if name.endswith("_pb2.py"):
        return True
    if name.endswith(".pb.go"):
        return True
    if name.endswith(".gen.ts") or name.endswith(".generated.ts"):
        return True
    if name.endswith(".gen.go") or name.endswith(".generated.go"):
        return True
    if name.endswith(".gen.py") or name.endswith(".generated.py"):
        return True
    if name.endswith(".gen.rs") or name.endswith(".generated.rs"):
        return True
    if name.endswith(".gen.java") or name.endswith(".generated.java"):
        return True
    return False


def _is_test_name(name: str, ext: str) -> bool:
    if ext == ".go" and name.endswith("_test.go"):
        return True
    if ".test." in name or ".spec." in name:
        return True
    if name.startswith("test_") or name.startswith("spec_"):
        return True
    return False


def _is_lockfile_name(name: str) -> bool:
    return name in LOCKFILE_NAMES or name.startswith("requirements") and name.endswith(".txt") or name.startswith("requirements_lock") and name.endswith(".txt") or name.startswith("conda-") and name.endswith(".lock") or name.startswith("environment") and (name.endswith(".lock") or name.endswith(".yml") or name.endswith(".yaml")) and "lock" in name


def _is_test_runner_name(name: str, lower_path: str) -> bool:
    if re.search(r"test.*runner|runner.*test", name):
        return True
    if re.search(r"import.*test|wpt.*import", name):
        return True
    if name.endswith(".sh") and ("test" in name or "wpt" in lower_path):
        return True
    if "/fuzzers/" in lower_path or "/fuzzer/" in lower_path:
        return True
    return False


def _is_generator_name(name: str, lower_path: str) -> bool:
    if name == "build.rs":
        return False
    stem = Path(name).stem.lower()
    if stem.startswith("generate_") or stem.startswith("gen_"):
        return True
    if "generator" in lower_path or "codegen" in lower_path:
        return True
    if "asmintgen" in lower_path or "tiffgenerator" in lower_path:
        return True
    if name.endswith("_gen.py") or name.endswith("_gen.go"):
        return False
    return False


def classifyFile(path: str, info: Optional[Dict[str, Any]] = None) -> FileClassification:
    lower_path = _lower_path(path)
    if not lower_path.startswith("/"):
        lower_path = "/" + lower_path
    parts = _path_parts_lower(path)
    name = _path_name_lower(path)
    stem = _path_stem(path)
    ext = _ext(info, path)

    fc = FileClassification()

    # 1. build.rs
    if name == "build.rs":
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    # 2. Lockfiles
    if name in LOCKFILE_NAMES:
        fc.role = "dependency_lock"
        fc.surface = "dependency_lock"
        fc.isDependencyLock = True
        fc.manualEditPolicy = "Dependency lockfile — validate dependency changes, do not refactor manually."
        fc.largeFilePolicy = "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
        return fc

    if name.startswith("requirements") and name.endswith(".txt"):
        fc.role = "dependency_lock"
        fc.surface = "dependency_lock"
        fc.isDependencyLock = True
        fc.manualEditPolicy = "Dependency/requirements file — validate dependency changes, do not refactor manually."
        fc.largeFilePolicy = "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
        return fc

    if name.startswith("requirements_lock") and name.endswith(".txt"):
        fc.role = "dependency_lock"
        fc.surface = "dependency_lock"
        fc.isDependencyLock = True
        fc.manualEditPolicy = "Locked requirements file — regenerate from source instead of editing manually."
        fc.largeFilePolicy = "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
        return fc

    if name == "pipfile.lock":
        fc.role = "dependency_lock"
        fc.surface = "dependency_lock"
        fc.isDependencyLock = True
        fc.manualEditPolicy = "Dependency lockfile — validate dependency changes, do not refactor manually."
        fc.largeFilePolicy = "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
        return fc

    if name.startswith("conda-") and name.endswith(".lock"):
        fc.role = "dependency_lock"
        fc.surface = "dependency_lock"
        fc.isDependencyLock = True
        fc.manualEditPolicy = "Conda environment lockfile — regenerate from environment.yml instead of editing manually."
        fc.largeFilePolicy = "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
        return fc

    # 3. Vendor
    vendor_prefixes = ("/vendor/", "/third_party/", "/third-party/", "/node_modules/", "/3rdparty/", "/external/")
    if any(lower_path.startswith(vp) or vp in lower_path for vp in vendor_prefixes):
        fc.role = "vendor"
        fc.surface = "vendor"
        fc.isVendor = True
        fc.manualEditPolicy = "Vendor/third-party file; track only, do not refactor by default."
        fc.largeFilePolicy = "Vendor/third-party file; track only, do not refactor by default."
        return fc

    # 4. Localization
    if "/i18n/" in lower_path or "/locales/" in lower_path or "/translations/" in lower_path or lower_path.startswith("/i18n/") or lower_path.startswith("/locales/") or lower_path.startswith("/translations/"):
        fc.role = "localization"
        fc.surface = "localization"
        fc.isLocalization = True
        fc.manualEditPolicy = "Large localization/resource file; large by design. Review only if translation loading, schema, or resource generation changes."
        fc.largeFilePolicy = "Large localization/resource file; large by design. Review only if translation loading, schema, or resource generation changes."
        return fc

    # 5. Generated code -- must come before test/doc checks
    if "/gen/" in lower_path or "/generated/" in lower_path:
        fc.role = "generated_sdk"
        fc.surface = "generated_sdk"
        fc.isGenerated = True
        fc.isGeneratedSdk = True
        fc.manualEditPolicy = "Generated SDK/client code — regenerate from schema/source instead of editing manually."
        fc.largeFilePolicy = "Generated SDK/client file; regenerate from schema/source instead of editing manually."
        return fc

    if _is_gen_name(name):
        fc.role = "generated_sdk"
        fc.surface = "generated_sdk"
        fc.isGenerated = True
        fc.isGeneratedSdk = True
        fc.manualEditPolicy = "Generated SDK/client code — regenerate from schema/source instead of editing manually."
        fc.largeFilePolicy = "Generated SDK/client file; regenerate from schema/source instead of editing manually."
        return fc

    if parts and parts[0].lower() in {"gen", "generated"}:
        fc.role = "generated_sdk"
        fc.surface = "generated_sdk"
        fc.isGenerated = True
        fc.isGeneratedSdk = True
        fc.manualEditPolicy = "Generated SDK/client code — regenerate from schema/source instead of editing manually."
        fc.largeFilePolicy = "Generated SDK/client file; regenerate from schema/source instead of editing manually."
        return fc

    if name in {"sdk.gen.ts", "types.gen.ts", "client.gen.ts", "sdk.gen.go", "types.gen.go", "client.gen.go"}:
        fc.role = "generated_sdk"
        fc.surface = "generated_sdk"
        fc.isGenerated = True
        fc.isGeneratedSdk = True
        fc.manualEditPolicy = "Generated SDK/client code — regenerate from schema/source instead of editing manually."
        fc.largeFilePolicy = "Generated SDK/client file; regenerate from schema/source instead of editing manually."
        return fc

    if "/src-tauri/gen/" in lower_path:
        fc.role = "vendor"
        fc.surface = "vendor"
        fc.isVendor = True
        fc.manualEditPolicy = "Vendor/third-party file; track only, do not refactor by default."
        fc.largeFilePolicy = "Vendor/third-party file; track only, do not refactor by default."
        return fc

    # 6. Documentation
    if ext == ".md":
        spec_keywords = {"spec", "design", "architecture", "adr", "proposal", "rfc"}
        is_spec = any(kw in name for kw in spec_keywords)
        is_in_specs = "/specs/" in lower_path or "/adr/" in lower_path or "/spec/" in lower_path
        if is_spec or is_in_specs:
            fc.role = "specification"
            fc.surface = "specification"
            fc.isSpecification = True
            fc.isDocumentation = True
            fc.manualEditPolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
            fc.largeFilePolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
            return fc
        if "/docs/" in lower_path or "/documentation/" in lower_path:
            fc.role = "documentation"
            fc.surface = "documentation"
            fc.isDocumentation = True
            fc.manualEditPolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
            fc.largeFilePolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
            return fc
        fc.role = "documentation"
        fc.surface = "documentation"
        fc.isDocumentation = True
        fc.manualEditPolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
        fc.largeFilePolicy = "Large documentation/specification file; review for readability and drift, not source module boundaries."
        return fc

    # 7. Fixtures (must come before general test check)
    if "/wpt-import/" in lower_path or "/fixtures/" in lower_path or "/testdata/" in lower_path:
        fc.role = "fixture"
        fc.surface = "test_data"
        fc.isFixture = True
        fc.manualEditPolicy = "Test/data fixture — useful for coverage, not a runtime refactor priority."
        fc.largeFilePolicy = "Large test file; review test structure only if frequently edited or flaky."
        return fc

    # 8. Tests (path-based first)
    if "/test/" in lower_path or "/tests/" in lower_path or "/e2e/" in lower_path or "/integration/" in lower_path or "/smoke/" in lower_path:
        if _is_test_name(name, ext):
            fc.role = "test"
            fc.surface = "test"
            fc.isTest = True
            fc.manualEditPolicy = "Test suite / test infrastructure."
            fc.largeFilePolicy = "Large test file; review test structure only if frequently edited or flaky."
            return fc
        if ext in {".json", ".txt", ".html", ".css", ".svg", ".xml", ".yaml", ".yml"}:
            fc.role = "fixture"
            fc.surface = "test_data"
            fc.isFixture = True
            fc.manualEditPolicy = "Test/data fixture — useful for coverage, not a runtime refactor priority."
            return fc
        if _is_test_runner_name(name, lower_path) or "runner" in name or "harness" in name:
            fc.role = "test_runner"
            fc.surface = "test_runner"
            fc.isTestRunner = True
            fc.manualEditPolicy = "Test runner / fuzzer infrastructure."
            fc.largeFilePolicy = "Large test file; review test structure only if frequently edited or flaky."
            return fc
        fc.role = "test_runner"
        fc.surface = "test_runner"
        fc.isTestRunner = True
        fc.manualEditPolicy = "Test runner / fuzzer infrastructure."
        fc.largeFilePolicy = "Large test file; review test structure only if frequently edited or flaky."
        return fc

    # 9. Test runners (before path-based test to catch runner infrastructure)
    if _is_test_runner_name(name, lower_path):
        fc.role = "test_runner"
        fc.surface = "test_runner"
        fc.isTestRunner = True
        fc.manualEditPolicy = "Test runner / fuzzer infrastructure."
        return fc

    # 10. Environment setup
    if "/.devcontainer/" in lower_path:
        fc.role = "environment_setup"
        fc.surface = "environment_setup"
        fc.isEnvironmentSetup = True
        fc.manualEditPolicy = "Environment/setup surface."
        fc.largeFilePolicy = "File is {lines} lines; environment/setup file."
        return fc

    # 11. Scripts (specific before general)
    if "/scripts/" in lower_path or "/script/" in lower_path:
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    if name.endswith(".sh") or name.endswith(".bash"):
        # CI and test scripts
        if "ci" in lower_path or "/ci/" in lower_path:
            fc.role = "build_tooling"
            fc.surface = "build_tooling"
            fc.isBuildTooling = True
            fc.manualEditPolicy = "CI/build infrastructure."
            return fc
        if _is_test_runner_name(name, lower_path):
            fc.role = "test_runner"
            fc.surface = "test_runner"
            fc.isTestRunner = True
            fc.manualEditPolicy = "Test runner script."
            return fc
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    # 12. Generators (separate from build_tooling)
    if _is_generator_name(name, lower_path):
        fc.role = "generator"
        fc.surface = "generator"
        fc.isGenerator = True
        fc.manualEditPolicy = "Code generator — run to regenerate outputs, do not edit outputs manually by default."
        return fc

    # 13. Build/tooling by name prefix
    if name.startswith("build.") or name.startswith("configure.") or name.startswith("install."):
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    # 14. CI paths
    if "/ci/" in lower_path:
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "CI/build infrastructure."
        return fc

    # 15. Ladybird-style Meta/ paths
    if "/meta/" in lower_path:
        if "/generators/" in lower_path:
            fc.role = "generator"
            fc.surface = "generator"
            fc.isGenerator = True
            fc.manualEditPolicy = "Code generator — run to regenerate outputs."
            return fc
        if "/linters/" in lower_path:
            fc.role = "build_tooling"
            fc.surface = "build_tooling"
            fc.isBuildTooling = True
            fc.manualEditPolicy = "Linter/build tooling."
            return fc
        if "/lagom/" in lower_path:
            if "/fuzzers/" in lower_path or "/fuzzer/" in lower_path:
                fc.role = "test_runner"
                fc.surface = "test_runner"
                fc.isTestRunner = True
                fc.manualEditPolicy = "Fuzzer infrastructure."
                return fc
            fc.role = "build_tooling"
            fc.surface = "build_tooling"
            fc.isBuildTooling = True
            fc.manualEditPolicy = "Build/tooling surface."
            return fc
        if "/cmake/" in lower_path:
            fc.role = "build_tooling"
            fc.surface = "build_tooling"
            fc.isBuildTooling = True
            fc.manualEditPolicy = "Build/tooling surface."
            return fc
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    # 16. Source code
    if ext in {".py", ".js", ".ts", ".rs", ".go", ".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".hh", ".hxx", ".java", ".kt", ".rb", ".cs", ".swift", ".zig", ".scala", ".ex", ".exs", ".ml", ".mli", ".fs", ".fsx"}:
        fc.role = "source"
        fc.surface = "runtime"
        fc.isRuntimeSource = True

        if _is_runtime_entry_name(name):
            fc.isRuntimeEntryCandidate = True
        elif stem in RUNTIME_HOTSPOT_NAMES:
            fc.isRuntimeHotspotCandidate = True
        elif re.search(r"/cmd/[^/]+/main\.(go|rs)$", lower_path):
            fc.isRuntimeEntryCandidate = True
        elif "src-tauri/src/main.rs" in lower_path:
            fc.isRuntimeEntryCandidate = True
        elif stem in {"bootstrap", "cli"} and not any(kw in lower_path for kw in ["/test/", "/tests/", "/spec/", "/docs/", "/fixtures/", "/examples/"]):
            fc.isRuntimeEntryCandidate = True

        return fc

    # 17. Config
    if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}:
        fc.role = "config"
        fc.surface = "config"
        fc.isConfig = True
        fc.manualEditPolicy = "Config/data file — validate schema before editing."
        fc.largeFilePolicy = "Large config/data file; validate schema before editing. Do not refactor like source code."
        return fc

    # 18. CMake files
    if name == "cmakelists.txt" or name == "cmake" or ext == ".cmake":
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "CMake build configuration."
        return fc

    # 19. Docker
    if name == "dockerfile" or name.startswith("docker-compose") or name == "containerfile":
        fc.role = "environment_setup"
        fc.surface = "environment_setup"
        fc.isEnvironmentSetup = True
        fc.manualEditPolicy = "Container/build environment."
        return fc

    # 20. Makefile
    if name == "makefile":
        fc.role = "build_tooling"
        fc.surface = "build_tooling"
        fc.isBuildTooling = True
        fc.manualEditPolicy = "Build/tooling surface."
        return fc

    fc.role = "source"
    fc.surface = "runtime"
    fc.isRuntimeSource = True
    return fc


def classifyRiskSurface(path: str, info: Optional[Dict[str, Any]] = None) -> str:
    fc = classifyFile(path, info)
    return fc.surface


def classifySurface(path: str, info: Optional[Dict[str, Any]] = None) -> str:
    return classifyFile(path, info).surface


def classifyRoleLabel(path: str, info: Optional[Dict[str, Any]] = None) -> str:
    fc = classifyFile(path, info)
    if fc.manualEditPolicy:
        return fc.manualEditPolicy
    return fc.role


def classifyLargeFilePolicy(path: str, info: Optional[Dict[str, Any]] = None) -> str:
    fc = classifyFile(path, info)
    if fc.largeFilePolicy:
        return fc.largeFilePolicy
    if fc.isDependencyLock:
        return "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
    if fc.isVendor:
        return "Vendor/third-party file; track only, do not refactor by default."
    if fc.isLocalization:
        return "Large localization/resource file; large by design. Review only if translation loading, schema, or resource generation changes."
    if fc.isGeneratedSdk:
        return "Generated SDK/client file; regenerate from schema/source instead of editing manually."
    if fc.isDocumentation or fc.isSpecification:
        return "Large documentation/specification file; review for readability and drift, not source module boundaries."
    if fc.isConfig:
        return "Large config/data file; validate schema before editing. Do not refactor like source code."
    if fc.isBuildTooling:
        return "File is {lines} lines; build/tooling file. Review if frequently modified."
    if fc.isEnvironmentSetup:
        return "File is {lines} lines; environment/setup file."
    if fc.isTest or fc.isTestRunner:
        return "Large test file; review test structure only if frequently edited or flaky."
    if fc.isFixture:
        return "Large test file; review test structure only if frequently edited or flaky."
    return "File is {lines} lines; consider reviewing module boundaries"


def classifyComponentRole(key: str) -> str:
    lower_key = key.lower()

    specific_roles = {
        "opencode": "CLI / AI coding agent core",
        "app": "frontend application",
        "desktop": "desktop application shell",
        "console": "console/web app",
        "sdk": "generated SDK/client code",
        "containers": "container/build tooling",
        "github": "GitHub integration",
        "infra": "infrastructure",
        "nix": "Nix/dev environment",
        "patches": "patches/platform fixes",
        "e2e": "end-to-end tests",
        "cmd": "CLI commands",
        "api": "API layer",
        "server": "server internals",
        "agent": "AI agent behaviors and coordination",
        "core": "runtime/core library",
        "shared": "shared utilities",
        "types": "type definitions",
        "ui": "user interface components",
        "mobile": "mobile application",
        "web": "web application",
        "cli": "CLI application",
        "utils": "utility modules",
        "python": "Python API layer",
        "compiler": "compiler/lowering components",
        "lite": "lightweight/mobile runtime",
        "c": "C API",
        "cc": "C++ API",
        "java": "Java API/bindings",
        "go": "Go bindings/tools",
        "tools": "developer/build tooling",
        "examples": "examples/sample apps",
        "ci": "CI/build infrastructure",
        "docs": "documentation",
        "tests": "test suite",
        "test": "test suite",
        "docker": "container/build environment",
        "config": "configuration",
        "scripts": "automation and tooling",
        "benchmarks": "benchmarks",
        "fuzz": "fuzz testing",
        "fuzzers": "fuzz testing",
        "plugins": "plugin system",
        "extensions": "extension system",
        "integrations": "integrations",
        "migrations": "database migrations",
        "seeds": "database seed data",
        "proto": "protobuf definitions",
        "graphql": "GraphQL schema/API",
        "grpc": "gRPC service definitions",
        "openapi": "OpenAPI specification",
        "specs": "specifications",
        "spec": "specifications",
        "adr": "architecture decision records",
        "proposals": "design proposals",
        "rfcs": "RFC documents",
        "vendor": "vendor dependency",
        "third_party": "vendor dependency",
        "external": "vendor dependency",
        "generated": "generated SDK/client code",
        "gen": "generated SDK/client code",
        "i18n": "internationalization/localization",
        "locales": "localization resources",
        "translations": "translation files",
    }

    if lower_key in specific_roles:
        return specific_roles[lower_key]

    if lower_key in {"demo-vault", "demo-vault-v2", "demo", "samples", "sample", "examples"}:
        return "demo/sample data"

    if lower_key in {"tests/libweb"}:
        return "test suite / WPT fixtures"
    if lower_key in {"tests/libjs"}:
        return "JavaScript engine tests"
    if lower_key in {"libraries/libweb"}:
        return "browser engine code"
    if lower_key in {"libraries/libgfx"}:
        return "graphics, fonts, and image codecs"
    if lower_key in {"libraries/libwasm"}:
        return "WebAssembly runtime and validation"
    if lower_key in {"libraries/libcore"}:
        return "platform and event-loop utilities"
    if lower_key in {"libraries/libmedia"}:
        return "media playback and container support"
    if lower_key in {"libraries/libwebview"}:
        return "browser embedding and view integration"
    if lower_key in {"libraries/libjs"}:
        return "JavaScript engine code"
    if lower_key in {"ak"}:
        return "core utility library"

    if "/" in key:
        parts = key.split("/")
        pkg_root = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""
        if pkg_root in MONOREPO_ROOTS:
            if sub in specific_roles:
                return specific_roles[sub]
            if sub.endswith("-electron") or sub.endswith("_electron"):
                return "Electron desktop shell"
            if "desktop" in sub:
                return "desktop application shell"
            if sub.endswith("-app") or sub.endswith("_app"):
                return "frontend application"
            if sub.endswith("-server") or sub.endswith("_server"):
                return "server runtime"
            if sub == "src-tauri":
                return "Tauri desktop backend"
            return f"{sub} package"
        if parts[0].lower() == "libraries" and len(parts) > 1:
            return _library_role(parts[1].lower())
        if parts[0].lower() == "tests" and len(parts) > 1:
            sub_lower = parts[1].lower()
            if sub_lower.startswith("libweb"):
                return "test suite / WPT fixtures"
            if sub_lower.startswith("libjs"):
                return "JavaScript engine tests"
            return "test suite / test infrastructure"

    if lower_key.startswith("libraries/lib"):
        lib_name = lower_key.split("/")[1][3:]
        return _library_role(lib_name)

    if lower_key.startswith("tests"):
        return "test suite"
    if lower_key.startswith("docs") or lower_key.startswith("documentation"):
        return "documentation"
    if lower_key.startswith(".devcontainer"):
        return "development environment"
    if lower_key.startswith("meta"):
        return "build and developer tooling"

    tail = key.split("/")[-1].lower()
    if tail in specific_roles:
        return specific_roles[tail]

    if "/" in key:
        parts = key.split("/")
        pkg_root = parts[0].lower()
        if pkg_root in MONOREPO_ROOTS and len(parts) > 1:
            return f"{parts[1]} package"

    return "application logic"


def _library_role(lib_name: str) -> str:
    roles = {
        "compress": "compression and archive codecs",
        "core": "platform and event-loop utilities",
        "crypto": "cryptography and certificate handling",
        "database": "database and storage layer",
        "devtools": "developer tools integration",
        "diff": "diff and patch utilities",
        "dns": "DNS protocol and resolution",
        "filesystem": "filesystem abstraction",
        "gc": "garbage collector infrastructure",
        "gfx": "graphics, fonts, and image codecs",
        "http": "HTTP networking stack",
        "idl": "IDL parsing and bindings support",
        "ipc": "interprocess communication",
        "js": "JavaScript engine code",
        "line": "command-line editing utilities",
        "main": "runtime entrypoint support",
        "media": "media playback and container support",
        "regex": "regular expression engine",
        "requests": "network request orchestration",
        "syntax": "syntax parsing and highlighting",
        "test": "test support library",
        "textcodec": "text encoding data and codecs",
        "threading": "threading and concurrency primitives",
        "tls": "TLS and secure transport",
        "unicode": "Unicode data and text processing",
        "url": "URL parsing and canonicalization",
        "wasm": "WebAssembly runtime and validation",
        "web": "browser engine code",
        "websocket": "WebSocket protocol support",
        "webview": "browser embedding and view integration",
        "xml": "XML parsing and DOM support",
        "imagedecoderclient": "image decoder client bindings",
    }
    return roles.get(lib_name, f"{lib_name} library")


def is_under_monorepo_root(path: str) -> bool:
    parts = _path_parts_lower(path)
    return bool(parts and parts[0] in MONOREPO_ROOTS)


def monorepo_component_key(path: str) -> str:
    parts = Path(path).parts
    if len(parts) < 2:
        return parts[0] if parts else path
    lower_parts = tuple(p.lower() for p in parts)
    top = lower_parts[0]
    if top in MONOREPO_ROOTS:
        return "/".join(parts[:2])
    if top in {"tests", "test"}:
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if top in {"docs", "doc", "documentation"}:
        return parts[0]
    if top == ".devcontainer":
        return parts[0]
    if top == "meta":
        if len(parts) >= 2 and lower_parts[1] in {"generators", "linters", "cmake"}:
            return "/".join(parts[:2])
        return parts[0]
    if top == "libraries":
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if top in {"services", "utilities", "ui", "cmd", "api", "server", "infra", "nix"}:
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if top in {"base", "patches", "github"}:
        return parts[0]
    if top in {"apps"}:
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if top in {"examples", "example", "samples", "sample"}:
        return parts[0]
    if top in {"benchmarks", "benchmark"}:
        return parts[0]
    if top in {"fuzz", "fuzzers", "fuzzer"}:
        return parts[0]
    if top in {"scripts", "tools", "build", "ci"}:
        return parts[0]
    if top in {"docker", "containers", "deploy"}:
        return parts[0]
    if top in {"i18n", "locales", "translations"}:
        return parts[0]
    if top in {"vendor", "third_party", "external", "3rdparty"}:
        return parts[0]
    if top in {"gen", "generated"}:
        return parts[0]
    return parts[0]


def is_runtime_entry_candidate_by_name(path: str) -> bool:
    name = _path_name_lower(path)
    stem = _path_stem(path)
    lower_path = _lower_path(path)
    if name in {"main.rs", "main.go", "main.ts", "index.ts", "index.js", "app.ts", "app.js", "server.ts", "server.js", "main.py", "server.py", "app.py", "cli.py", "cli.ts", "main.cpp", "main.cc"}:
        return True
    if re.search(r"/cmd/[^/]+/main\.(go|rs)$", lower_path):
        return True
    if "src-tauri/src/main.rs" in lower_path:
        return True
    if stem in {"bootstrap", "cli"} and not any(kw in lower_path for kw in ["/test/", "/tests/", "/spec/", "/docs/", "/fixtures/", "/examples/"]):
        return True
    return False
