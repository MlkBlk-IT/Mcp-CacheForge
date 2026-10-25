import io, sys
dash = r"D:\CacheForge\static\dashboard.html"
new  = r"D:\CacheForge\_loop3d_new.js"
with io.open(dash, "r", encoding="utf-8") as f:
    lines = f.readlines()
# locate the old torus-gauge block boundaries (1-indexed 2151..2460 expected)
start = None
for i, l in enumerate(lines):
    if "3D TORUS GAUGE" in l:
        start = i - 1  # the opening /* === line above
        break
if start is None:
    print("MARKER NOT FOUND"); sys.exit(1)
# find the end: the line "const RING_LIME" then the class end
end = None
for j in range(start, len(lines)):
    if lines[j].rstrip() == "}" and "const RING" in lines[start+1] or (lines[j].rstrip() == "}" and j > start + 50):
        end = j
        break
if end is None:
    print("END NOT FOUND"); sys.exit(1)
print("replacing lines", start+1, "..", end+1)
print("first:", lines[start].rstrip())
print("last :", lines[end].rstrip())
with io.open(new, "r", encoding="utf-8") as f:
    block = f.read()
if not block.endswith("\n"):
    block += "\n"
newlines = lines[:start] + [block] + lines[end+1:]
with io.open(dash, "w", encoding="utf-8", newline="") as f:
    f.writelines(newlines)
print("OK, new total lines:", len(newlines))
