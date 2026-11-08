"""Entry point of the portable exe: the window, or (with --elevated) the admin helper the window starts through UAC."""
import sys

if len(sys.argv) >= 4 and sys.argv[1] == '--elevated':
    from vbios_studio.elevated import main as elevated
    elevated(sys.argv[2], sys.argv[3])
else:
    from vbios_studio.app import main
    sys.exit(main())
