# How a transfer works today

```mermaid
flowchart TD
    A["<b>Job A</b> — staged at a property<br/>labels printed, items picked, installed"] --> B{"Client keeps<br/>the stock?"}

    B -->|"No — back to the warehouse"| C["Pickup job A<br/>everything returns"]
    B -->|"Yes — goes to another property"| D["<b>Upload Job B's packing slip</b><br/>tick 'This is a transfer'<br/>and choose Job A as the source"]

    D --> E["Job B created<br/>is_transfer = true<br/>transfer_from_job_id = A"]

    E --> F["<b>Stylist, on Job A</b><br/>marks the items that are NOT going<br/>not_transferring = true"]
    E --> G["<b>Stylist, on Job B</b><br/>marks the items arriving from A<br/>is_transfer_item = true"]

    F --> H["<b>Pickup day — Job A</b>"]
    G --> I["<b>Install day — Job B</b>"]

    H --> H1["Driver page shows<br/>'Transferring To → Job B'<br/>(job number links to B)"]
    H --> H2["Items marked Not Transferring<br/>listed separately — they stay behind"]
    H --> H3["Everything else is loaded<br/>and driven straight to B"]

    I --> I1["Driver page shows<br/>'Transfer From → Job A'<br/>(job number links to A)"]
    I --> I2["Items marked Transfer are<br/>already on the truck —<br/>excluded from the loading count"]
    I --> I3["The rest is picked and loaded<br/>from the warehouse as normal"]

    H3 --> J["Stock moves A → B directly<br/>never returning to the warehouse"]
    I2 --> J
    J --> K["<b>Job B installed</b><br/>later collected as a normal pickup"]

    style D fill:#B8935A,color:#fff
    style E fill:#B8935A,color:#fff
    style F fill:#6A3D8A,color:#fff
    style G fill:#6A3D8A,color:#fff
    style J fill:#4A7C59,color:#fff
    style K fill:#4A7C59,color:#fff
```

## The two flags, and why both exist

| Flag | Set on | Means |
|---|---|---|
| `is_transfer` + `transfer_from_job_id` | **Job B** (the receiving job) | This whole job draws stock from Job A |
| `not_transferring` | items on **Job A** | Stays at the property / returns to the warehouse — not part of the move |
| `is_transfer_item` | items on **Job B** | Arrives from A, already on the truck — don't load it from the warehouse |

The link is recorded on B only. A knows it is sending stock because another
job points at it — which is why the "Transferring To" banner is a lookup
rather than a stored field.

## Where it shows up

| Screen | What it shows |
|---|---|
| Driver — Job A (pickup) | "Transferring To → B", Not Transferring items listed apart |
| Driver — Job B (install) | "Transfer From → A", transfer items excluded from the load count |
| Scheduler tile | TRANSFER FROM / TO, with the partner's reference and street |
| Team view card | ⇄ Transfer from / to, same wording |
| Summary PDF | transfer markings per item, splitting repeat groups |

## Known gaps

- **Nothing verifies the two sides agree.** If Job A has no items marked
  not-transferring and B has none marked as transfers, the link still
  exists but no stock is actually accounted for as moving.
- **A chain works but isn't shown as one.** A → B → C is two separate
  links; no screen shows the full chain.
- **Only one source per job.** Job B can draw from exactly one job.
