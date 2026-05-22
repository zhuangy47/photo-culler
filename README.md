## what is this
photo mechanic is expensive. fortunately, i have a computer science degree and a twenty dollar claude subscription. the latter is doing most of the heavy lifting.

a fast-ish, simple photo culler that is tailored is exactly my workflow. it's also free.

## how do i set it up
1. get python
2. `pip install .`. you should probably do this in a venv.
3. `python3 run_culler.py`
4. profit

i'm working on building it for macos and windows. life has been busy :/

## how does the app work
you create culling "sessions". in each session you open folders with photos. files with the same name in the same folder are moved as one unit.
arrows keys to move between images. 
brackets to zoom.
ctrl/cmd+arrows to pan.
ctrl/cmd+shift+1-9 to save the current zoom level.
ctrl/cmd+1-9 to recall that zoom level.
ctrl/cmd+0 to reset zoom/pan.
ctrl+L toggles sticky mode (keep zoom between images)
ctrl/cmd+Z to undo last cull.

the goal is for this culler to be easy to use with only the keyboard and to be faster than lightroom and at least as fast as photo mechanic.

i tried to do some clever caching things. it kinda works™
