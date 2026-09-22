# Local performance comparison — 1.8

before: 10000 updates; 10000 redraws; 0.0858s processing with captured output
after: 10000 updates; 1 redraws; 0.0402s processing with captured output
before: 2 destination query/queries per existing-file decision
after: 1 destination query/queries per existing-file decision
Block 1 MiB: median 0.0352s for 32 MiB on local disk/cache (3 runs)
Block 4 MiB: median 0.0392s for 32 MiB on local disk/cache (3 runs)
Block 8 MiB: median 0.0276s for 32 MiB on local disk/cache (3 runs)
Block 16 MiB: median 0.0280s for 32 MiB on local disk/cache (3 runs)

Screen measurements cover Python processing with captured output, not CMD rendering. Block-size tests use local disk/cache and do not establish NAS performance. The 8 MiB default is unchanged pending real Windows/NAS measurements.
