## Table 5.X: Validation Probe Detection Matrix

| Defect | Class | SHACL Catches | Compliance Catches |
|--------|-------|:-------------:|:------------------:|
| S1: Missing assigned agent | Structural | Yes | --- |
| S2: Empty capabilities | Structural | Yes | --- |
| S3: Missing description | Structural | Yes | --- |
| S4: Hazmat task without approval | Structural | Yes | --- |
| S5: Cold storage task without temp range | Structural | Yes | --- |
| S6: Critical deal without high risk | Structural | Yes | --- |
| D1: FreshCo → Bavaria (no cold storage) | Domain | --- | Yes |
| D2: GreenLeaf → Centro Bologna (no hazmat, standard security) | Domain | --- | Yes |
| D3: GreenLeaf → LogisPark Milano (no hazmat) | Domain | --- | Yes |
| D4: QuickShip → PharmaStore Lyon (valid pairing) | Domain | --- | --- |

### Detection Summary

|  | Structural Defects | Domain Defects |
|---|:---:|:---:|
| **SHACL catches** | 6/6 | 0/4 |
| **Compliance catches** | 0/6 | 3/4 |

**Conclusion**: Zero overlap -- SHACL catches all structural defects; compliance catches all domain defects. Layers are empirically complementary.