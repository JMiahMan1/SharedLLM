"""The studio has to know what it is looking at before it runs anything.

Each stack's commands are asserted literally, because "run the tests" is only safe
when the command comes from the profile and not from a model's guess. The
ordering tests matter too: a Vite app that also has a requirements.txt is still a
Node project.
"""
from __future__ import annotations

from services.gateway.studio import profiles


def test_a_vite_app_is_node_even_with_a_stray_requirements_file():
    profile = profiles.detect(["package.json", "vite.config.ts", "requirements.txt", "src/main.tsx"])

    assert profile.stack == "node-vite"
    assert profile.preview_port == 5173
    assert profile.steps["test"].command == "npm test"
    assert profile.steps["build"].command == "npm run build"
    assert profile.steps["serve"].command.endswith("--host 0.0.0.0")


def test_the_lockfile_chooses_the_runner():
    assert profiles.detect(["package.json", "pnpm-lock.yaml"]).steps["install"].command == "pnpm install"
    assert profiles.detect(["package.json", "yarn.lock"]).steps["install"].command == "yarn install"
    assert (
        profiles.detect(["package.json", "package-lock.json"]).steps["install"].command
        == "npm install --no-audit --no-fund"
    )


def test_next_js_previews_on_3000():
    profile = profiles.detect(["package.json", "next.config.mjs"])

    assert profile.stack == "node-next"
    assert profile.preview_port == 3000


def test_python_runs_its_own_pytest_and_compiles():
    profile = profiles.detect(["pyproject.toml", "requirements.txt", "app.py"])

    assert profile.stack == "python"
    assert profile.steps["test"].command == "python -m pytest -q"
    assert profile.steps["build"].command.startswith("python -m compileall")
    assert profile.steps["install"].command.startswith("pip install -q -r requirements.txt")


def test_go_rust_java_and_dotnet_are_detected_with_real_commands():
    go = profiles.detect(["go.mod", "main.go"])
    assert (go.stack, go.steps["test"].command) == ("go", "go test ./...")

    rust = profiles.detect(["Cargo.toml", "src/main.rs"])
    assert (rust.stack, rust.steps["test"].command) == ("rust", "cargo test")

    maven = profiles.detect(["pom.xml"])
    assert maven.stack == "java-maven" and maven.steps["test"].command == "mvn -q test"

    gradle = profiles.detect(["build.gradle", "gradlew"])
    assert gradle.stack == "java-gradle" and gradle.steps["test"].command == "./gradlew test"

    dotnet = profiles.detect(["App.sln", "App/App.csproj"])
    assert dotnet.stack == "dotnet" and dotnet.steps["test"].command == "dotnet test"


def test_a_folder_of_html_is_a_static_site_with_a_preview():
    profile = profiles.detect(["index.html", "style.css", "photo.jpg"])

    assert profile.stack == "static"
    assert profile.preview_port == 8080
    assert "http.server" in profile.steps["serve"].command


def test_an_empty_or_document_only_workspace_is_honest_about_it():
    profile = profiles.detect(["sermon.md", "notes/idea.txt"])

    assert profile.stack == "unknown"
    assert profile.steps == {}
    assert any("no project file found" in note for note in profile.notes)


def test_every_stack_present_is_listed():
    assert profiles.all_stacks(["go.mod", "package.json"]) == ["node", "go"]
    assert profiles.all_stacks(["sermon.md"]) == []


def test_the_profile_is_json_ready():
    payload = profiles.detect(["package.json"]).to_dict()

    assert payload["stack"] == "node"
    assert payload["steps"]["test"]["command"] == "npm test"
    assert isinstance(payload["notes"], list)


def test_slugs_make_safe_folder_names():
    assert profiles.slug("Star Catcher: Deluxe!") == "star-catcher-deluxe"
    assert profiles.slug("   ") == "untitled"
