# Release Process

## Pre-release Checklist

1. **Documentation consistency**
   - Terminology consistent across all docs (SKILL.md, README.md, docs/*.md)
   - Examples in docs match current CLI behavior
   - Version references updated

2. **CLI consistency**
   - `keep --help` output matches documentation
   - All command examples in docs actually work
   - Help text uses consistent terminology

3. **Version bump**
   - `SKILL.md` frontmatter: `version: X.Y.Z`
   - `pyproject.toml`: `version = "X.Y.Z"`

4. **Tests**
   ```bash
   pytest
   ```

## Release

1. **Commit and tag**
   ```bash
   git add -A
   git commit -m "Release X.Y.Z - Brief description"
   git tag vX.Y.Z
   git push origin main --tags
   ```

2. **Build**
   ```bash
   rm -rf dist/
   python -m build
   ```

3. **Publish to PyPI**
   ```bash
   twine upload dist/*
   ```

4. **GitHub release**
   - Create release from tag at https://github.com/hughpyle/keep/releases
   - Copy relevant changelog items to release notes
