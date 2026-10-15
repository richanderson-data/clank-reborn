# Disposable autonomy demo

This project contains an intentional bug in calc.py and three real pytest tests.
Copy this folder before trying it so the original example stays broken.

Connect the copy with --autonomous and enable Autonomous in the browser.
Ask: "Run the tests, fix the addition bug, rerun until all three pass, and
report exactly what changed." No extra dependencies beyond pytest are needed.

Changes & undo can restore the original file. Tests execute project code with
your OS permissions; this example is deliberately small and inspectable.
