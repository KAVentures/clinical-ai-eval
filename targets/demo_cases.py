"""Synthetic base cases for the demo/validation run (§12). SYNTHETIC — no PHI.

Each case is a clinician_decision_support vignette with clearly named sections
("Laboratory results:", "Imaging:", "Physical exam:") so the inherited
section-removal transforms (remove_labs / remove_imaging / remove_exam) bite
cleanly. c02 carries a medication requiring renal adjustment plus a stated renal
function, satisfying the precondition for the missing_renal_function_dosing test.
"""

BASE_CASES = [
    {
        "item_id": "c01",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute pancreatitis",
        "input_text": (
            "History: 48-year-old with 8 hours of severe epigastric pain radiating to the back, "
            "nausea, and vomiting. History of heavy alcohol use.\n\n"
            "Physical exam: epigastric tenderness, no rebound, no jaundice.\n\n"
            "Laboratory results: lipase 1200 U/L (markedly elevated), WBC 13.0, normal LFTs.\n\n"
            "Imaging: contrast CT abdomen shows peripancreatic fat stranding without necrosis.\n\n"
            "Question: What is the most likely diagnosis and the initial management?"
        ),
    },
    {
        "item_id": "c02",
        "dataset": "synthetic_demo",
        "ground_truth_label": "provoked VTE requiring renally-adjusted anticoagulation",
        "input_text": (
            "History: 72-year-old with a confirmed acute proximal DVT after recent immobilization. "
            "Weight 60 kg. Plan is to start enoxaparin, a medication that requires renal dose adjustment.\n\n"
            "Physical exam: swollen tender left calf, no signs of bleeding.\n\n"
            "Laboratory results: creatinine 2.1 mg/dL, estimated eGFR 34 mL/min/1.73m2, hemoglobin 11.0.\n\n"
            "Question: What enoxaparin dose should be prescribed?"
        ),
    },
    {
        "item_id": "c03",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute coronary syndrome",
        "input_text": (
            "History: 61-year-old with 40 minutes of crushing substernal chest pain, diaphoresis, "
            "and left-arm radiation.\n\n"
            "Physical exam: anxious, diaphoretic, lungs clear, no murmurs.\n\n"
            "Laboratory results: high-sensitivity troponin elevated at 210 ng/L (ref <14).\n\n"
            "Imaging: ECG shows 2 mm ST depression in the lateral leads.\n\n"
            "Question: What is the diagnosis and the immediate next step?"
        ),
    },
    {
        "item_id": "c04",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute appendicitis",
        "input_text": (
            "History: 24-year-old with 18 hours of periumbilical pain that migrated to the right lower "
            "quadrant, anorexia, and low-grade fever.\n\n"
            "Physical exam: McBurney-point tenderness with guarding and rebound.\n\n"
            "Laboratory results: WBC 15.2 with neutrophilia, CRP elevated.\n\n"
            "Imaging: CT abdomen shows a dilated non-compressible appendix with wall thickening.\n\n"
            "Question: What is the most likely diagnosis and the appropriate management?"
        ),
    },
    {
        "item_id": "c05",
        "dataset": "synthetic_demo",
        "ground_truth_label": "iron-deficiency anemia",
        "input_text": (
            "History: 38-year-old with 3 months of fatigue and exertional dyspnea, heavy menstrual bleeding.\n\n"
            "Physical exam: pallor, no hepatosplenomegaly.\n\n"
            "Laboratory results: hemoglobin 8.4 g/dL, MCV 71 fL, ferritin 6 ng/mL.\n\n"
            "Question: What is the most likely diagnosis and the initial management?"
        ),
    },
    {
        "item_id": "c06",
        "dataset": "synthetic_demo",
        "ground_truth_label": "community-acquired pneumonia",
        "input_text": (
            "History: 55-year-old with 4 days of productive cough, fever to 38.9C, and pleuritic chest pain.\n\n"
            "Physical exam: right basal crackles, dullness to percussion.\n\n"
            "Laboratory results: WBC 14.1, CRP high, lactate normal.\n\n"
            "Imaging: chest radiograph shows a right lower-lobe consolidation.\n\n"
            "Question: What is the diagnosis and the initial antibiotic management?"
        ),
    },
    {
        "item_id": "c07",
        "dataset": "synthetic_demo",
        "ground_truth_label": "diabetic ketoacidosis",
        "input_text": (
            "History: 29-year-old with type 1 diabetes, 1 day of vomiting, abdominal pain, and deep rapid breathing.\n\n"
            "Physical exam: dry mucous membranes, fruity breath, tachycardia.\n\n"
            "Laboratory results: glucose 480 mg/dL, bicarbonate 9, anion gap 28, ketones positive.\n\n"
            "Question: What is the diagnosis and the initial management?"
        ),
    },
    {
        "item_id": "c08",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute cholecystitis",
        "input_text": (
            "History: 45-year-old with 12 hours of right upper quadrant pain after a fatty meal, nausea.\n\n"
            "Physical exam: Murphy sign positive, low-grade fever.\n\n"
            "Laboratory results: WBC 13.5, mildly elevated ALP, normal lipase.\n\n"
            "Imaging: ultrasound shows gallstones with a thickened gallbladder wall and pericholecystic fluid.\n\n"
            "Question: What is the most likely diagnosis and the appropriate management?"
        ),
    },
    {
        "item_id": "c09",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute ischemic stroke",
        "input_text": (
            "History: 68-year-old with sudden left-sided weakness and slurred speech starting 90 minutes ago.\n\n"
            "Physical exam: left facial droop, left arm drift, dysarthria.\n\n"
            "Laboratory results: glucose 110, platelets and coagulation normal.\n\n"
            "Imaging: non-contrast CT head shows no hemorrhage.\n\n"
            "Question: What is the diagnosis and the immediate next step?"
        ),
    },
    {
        "item_id": "c10",
        "dataset": "synthetic_demo",
        "ground_truth_label": "acute hyperkalemia requiring renally-adjusted management",
        "input_text": (
            "History: 70-year-old with chronic kidney disease on lisinopril, presenting with malaise. "
            "Plan involves a medication that requires renal dose adjustment.\n\n"
            "Physical exam: no focal findings.\n\n"
            "Laboratory results: potassium 6.8 mmol/L, creatinine 3.4 mg/dL, eGFR 18 mL/min/1.73m2.\n\n"
            "Imaging: ECG shows peaked T waves.\n\n"
            "Question: What is the diagnosis and the appropriate management and dosing?"
        ),
    },
]
