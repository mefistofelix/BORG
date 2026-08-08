#import smoothpy
#smoothpy.install_import_hook()

import os
import sys
from . import x

bd = os.path.dirname(__file__)
pd = os.path.dirname(bd)
#ld = rf"{bd}/lib"
#sys.dont_write_bytecode = True
#sys.path.append(ld)
#__package__ = ""

#print(rd)
from . import borg
x.go_main(borg.run(pd))
#borg.borg()
