{
  lib,
  python3Packages,
  playwright-driver,
}:
let
  # Only the headless shell: the browser is used just to pass Reddit's bot check.
  browsers = playwright-driver.browsers.override {
    withChromium = false;
    withFirefox = false;
    withWebkit = false;
    withFfmpeg = false;
  };
in
python3Packages.buildPythonApplication {
  pname = "archivvit";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../archivvit
      ../tests
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [
    playwright
    curl-cffi
    lxml
    cssselect
  ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  makeWrapperArgs = [
    "--set"
    "PLAYWRIGHT_BROWSERS_PATH"
    "${browsers}"
    "--set"
    "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS"
    "true"
  ];

  passthru = { inherit browsers; };

  meta.mainProgram = "archivvit";
}
