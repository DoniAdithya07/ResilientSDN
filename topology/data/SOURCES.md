# Topology data sources

| File | Network | Source | Licence |
|------|---------|--------|---------|
| `internet2_os3e.yaml` | Internet2 OS3E backbone, 34 nodes / 42 links | Link list and geocoded coordinates from B. Heller, *The Controller Placement Problem*, HotSDN 2012 ([github.com/brandonheller/cpp](https://github.com/brandonheller/cpp), `src/topo/os3e.py`, `src/geo/os3e_latlong.json`), transcribed from the Internet2 OS3E map | Facts (city locations, adjacency); cite Heller et al. |
| `Geant2012.graphml` | GÉANT 2012, 40 nodes / 61 links | Internet Topology Zoo ([github.com/mroughan/InternetTopologyZoo](https://github.com/mroughan/InternetTopologyZoo)); S. Knight et al., *The Internet Topology Zoo*, IEEE JSAC 29(9), 2011 | CC BY 4.0 |
| `Nsfnet.graphml` | NSFNET T3 backbone (1991), 13 nodes / 15 links | Internet Topology Zoo, `graphml/Nsfnet.graphml` (downloaded from [github.com/mroughan/InternetTopologyZoo](https://github.com/mroughan/InternetTopologyZoo)); source map nthelp.com/images/nsf.jpg | CC BY 4.0 |

## Gaps filled by us (see `config/network_config.yaml`)

- **Internet2:** the OS3E map gives no per-link speeds; all links are 100 Gbps (OS3E was a 100G backbone).
- **GÉANT:** 22 of 61 links have no speed. They are the lit-fibre core (e.g. NL–DE, FR–UK, DE–CH) and are set to 10 Gbps, the highest labelled speed in the file.
- **GÉANT:** UA, MD and BY have no coordinates; their capitals are used.
- The Topology Zoo file named `Internet2.graphml` is **not** used: it lists 20 regional networks with no locations or speeds, not the 34-node backbone.
- **NSFNET:** the file has no link speeds; all 15 links are set to the same speed (10 Gbps -> 100 Mbps after scaling). Every node has coordinates.
