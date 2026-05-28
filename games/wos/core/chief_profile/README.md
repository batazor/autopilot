# Chief Profile Compatibility Alias

This directory keeps old editor paths working after the chief profile DSL moved
under `games/wos/core/who_i_am`.

Do not add `module.yaml` here. The live module remains `who_i_am`; these files
are symlinks only so module and scenario discovery do not load a duplicate copy.
