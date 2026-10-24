# Traffic Vehicle Detection Annotation Guideline v1.2

- Version: 1.2
- Status: CONTROLLED OPERATIONAL CLARIFICATION BASED ON BATCH 1 HUMAN-REVIEW EVIDENCE; v1.1 is retained unchanged
- This revision records a motorcycle-boundary clarification supported by Batch 1 human visual review. It does not change the roadway-only scope or add or remove any of the four project classes.

## 1. Scope

Formal Annotation Guideline v1.1 formally clarifies and standardises the roadway-only spatial annotation scope that was already applied operationally during the Pilot human-annotation process. It is not a new Formal-stage scope change. Include vehicles in active traffic lanes, intersections, through lanes, turning lanes, traffic-signal queues, congestion queues, and temporarily stopped vehicles participating in traffic.

Exclude vehicles in parking lots, marked parking spaces, designated curbside parking, private parking, and clearly non-road traffic areas. A stationary vehicle is not automatically excluded: a vehicle stopped at a red light or in a congestion queue remains in scope.

## 2. Classes

Temporarily retain four project classes:

```text
0 = car
1 = motorcycle
2 = bus
3 = truck
```

After Formal Train Batch 1 completes human verification, the motorcycle class scope will be reassessed using human-verified class-support evidence. Motorcycle remains provisionally available now.

## Project Class Boundary Rules

These rules clarify the operational boundaries of the four existing project classes. They do not change the approved roadway-only scope and do not add or remove classes.

- Sedan, hatchback, SUV, and minivan are car.
- Pickup, box truck, lorry, and a tractor-trailer when the tractor and trailer are connected are truck.
- Public bus, school bus, coach, and a visually clear minibus are bus.
- Emergency vehicles are classified according to their basic body type.
- For each reliably identifiable motorcycle, create one motorcycle annotation. Do not annotate the rider separately and do not create a person class. The rectangle must follow the reliably identifiable visible extent of the motorcycle. Because the rider and motorcycle may overlap, the HBB may unavoidably include some rider pixels; do not enlarge the box merely to include the rider's head, torso, or limbs, and do not crop the box to remove all rider pixels if that would cut off the wheels, body, or handlebars. For targets that are too small, blurry, or severely occluded, annotate only when motorcycle can be reliably distinguished from bicycle, person, or another object; otherwise record QA_REVIEW_REQUIRED and do not guess.
- If car, motorcycle, bus, or truck cannot be distinguished reliably, do not guess or force a class. Record QA_REVIEW_REQUIRED.
- For an occluded vehicle, annotate only the reliably confirmable visible vehicle extent; do not infer fully hidden portions.
- For an edge-truncated vehicle, keep the rectangular bounding box inside the image boundary.
- All annotations remain rectangle-only horizontal bounding boxes (HBB). Do not use polygons, segmentation, or oriented bounding boxes (OBB).

## 3. YOLO-assisted annotation

X-AnyLabeling is the preferred formal annotation interface, automatic pre-label interface, and human correction interface. YOLO predictions are annotation candidates only, never ground truth.

Workflow: YOLO pre-label → human review → add missed roadway vehicles → delete false positives → delete out-of-scope parking/non-road vehicles → correct classes and boxes → final human verification.

Human review is mandatory for every image. Do not automatically delete or retain a vehicle based on confidence, pHash, or a roadway ROI.

## 4. COCO-to-project mapping

The mapping is explicit and project labels must not use COCO IDs directly:

```text
COCO 2 (car)        -> project 0 (car)
COCO 3 (motorcycle) -> project 1 (motorcycle)
COCO 5 (bus)        -> project 2 (bus)
COCO 7 (truck)      -> project 3 (truck)
```

## 5. Boxes and difficult cases

Use one tight, axis-aligned box per identifiable vehicle. Include identifiable occluded, truncated, small, and distant vehicles when a human can reliably determine the class. Do not guess when the class is indeterminate. Delete detector boxes on structures, poles, signs, lights, roads, buildings, trees, shadows, reflections, or other non-vehicles.

## 6. ROI and parking handling

Automatic active-roadway ROI filtering is deferred and is not part of current formal annotation. Do not use segmentation, lane detection, inferred roadway polygons, or automatic active-road masks.

Do not automatically filter parking-area vehicles. The human reviewer applies the scope rule. During formal annotation, if continuing evidence shows that parking predictions create a substantial repeated manual burden, a separate **Conservative Parking Exclusion Zones v1** may be proposed. Any such zones must be manually defined, conservative, clearly parking-only, human-approved, and must not replace human verification.

## 7. Formats and auditability

Working annotations should remain in LabelMe-compatible JSON or an equivalent auditable record. Final verified training annotations should be exported as YOLO TXT. Existing pilot files must not be overwritten.

## 8. Confidence and checkpoint

Confidence is configurable and is not frozen from detector-evaluation work. After the technical smoke test confirms that the toolchain runs correctly, practical pre-label confidence may be recorded during Formal Train Batch 1 according to human correction burden; it must not be claimed statistically optimal. A general COCO-pretrained YOLO11 detector is the proposed initial checkpoint. The pilot fine-tuned model is not presumed superior because its results were mixed and it contained no motorcycle ground-truth instances.
