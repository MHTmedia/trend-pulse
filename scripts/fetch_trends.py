"""
TrendPulse nightly fetch script.
- Loads dynamic keyword list from cache/keywords.json (falls back to defaults)
- Fetches Google Trends + Reddit + Amazon data for all active keywords
- Detects fading trends and marks them
- Discovers new rising keywords via pytrends related_queries
- Writes updated cache/keywords.json and cache/trends.json

Run locally or via GitHub Actions. Do NOT run on Railway (datacenter IPs get blocked).

Usage:
    pip install pytrends requests beautifulsoup4 lxml
    python scripts/fetch_trends.py
"""

import json
import time
import logging
import re
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from pytrends.request import TrendReq

# ── Paths ─────────────────────────────────────────────────────────────────────
CACHE_DIR      = Path("cache")
TRENDS_FILE    = CACHE_DIR / "trends.json"
KEYWORDS_FILE  = CACHE_DIR / "keywords.json"

# ── Config ────────────────────────────────────────────────────────────────────
GEO       = "US"
TIMEFRAME = "today 12-m"

# Fading: peak must be notable AND recent months must be well below it
FADING_PEAK_MIN      = 25   # ignore flat/low keywords (noise)
FADING_RECENT_RATIO  = 0.45  # recent 3-mo avg must be < 45% of peak
FADING_SLOPE_WINDOW  = 4    # look at last N months for declining slope

# Discovery: how many new keywords to add per run (caps runaway growth)
MAX_NEW_PER_RUN = 20

# Reddit
REDDIT_HEADERS = {"User-Agent": "TrendPulse/1.0 (trend research tool)"}

# Amazon — rotate UAs to reduce fingerprinting
AMAZON_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
AMAZON_SESSION = requests.Session()   # reuse TCP connection within a run

# ── Default keyword list (used only if keywords.json doesn't exist yet) ───────
DEFAULT_KEYWORDS = [
    # ── Health & Wellness (50) ─────────────────────────────────────────────────
    {"keyword": "Creatine Gummies",              "category": "Health & Wellness"},
    {"keyword": "Mushroom Coffee",               "category": "Health & Wellness"},
    {"keyword": "Collagen Peptides Powder",      "category": "Health & Wellness"},
    {"keyword": "Hydrogen Water Bottle",         "category": "Health & Wellness"},
    {"keyword": "Grounding Mat",                 "category": "Health & Wellness"},
    {"keyword": "Gut Health Test Kit",           "category": "Health & Wellness"},
    {"keyword": "Magnesium Glycinate",           "category": "Health & Wellness"},
    {"keyword": "Berberine Supplement",          "category": "Health & Wellness"},
    {"keyword": "Methylene Blue Supplement",     "category": "Health & Wellness"},
    {"keyword": "Shilajit Supplement",           "category": "Health & Wellness"},
    {"keyword": "Sea Moss Gel",                  "category": "Health & Wellness"},
    {"keyword": "Tallow Balm",                   "category": "Health & Wellness"},
    {"keyword": "Beef Liver Supplement",         "category": "Health & Wellness"},
    {"keyword": "Electrolyte Powder",            "category": "Health & Wellness"},
    {"keyword": "Adaptogen Supplements",         "category": "Health & Wellness"},
    {"keyword": "NAD Supplement",                "category": "Health & Wellness"},
    {"keyword": "Spermidine Supplement",         "category": "Health & Wellness"},
    {"keyword": "Peptide Supplement",            "category": "Health & Wellness"},
    {"keyword": "Urolithin A Supplement",        "category": "Health & Wellness"},
    {"keyword": "Mouth Tape Sleep",              "category": "Health & Wellness"},
    {"keyword": "NMN Supplement",                "category": "Health & Wellness"},
    {"keyword": "Lion's Mane Supplement",        "category": "Health & Wellness"},
    {"keyword": "Turkey Tail Mushroom",          "category": "Health & Wellness"},
    {"keyword": "Reishi Mushroom Supplement",    "category": "Health & Wellness"},
    {"keyword": "Cordyceps Supplement",          "category": "Health & Wellness"},
    {"keyword": "Glutathione Supplement",        "category": "Health & Wellness"},
    {"keyword": "Quercetin Supplement",          "category": "Health & Wellness"},
    {"keyword": "Fisetin Supplement",            "category": "Health & Wellness"},
    {"keyword": "Resveratrol Supplement",        "category": "Health & Wellness"},
    {"keyword": "Alpha Lipoic Acid",             "category": "Health & Wellness"},
    {"keyword": "Vitamin D3 K2 Supplement",      "category": "Health & Wellness"},
    {"keyword": "Boron Supplement",              "category": "Health & Wellness"},
    {"keyword": "Iodine Supplement",             "category": "Health & Wellness"},
    {"keyword": "Lithium Orotate Supplement",    "category": "Health & Wellness"},
    {"keyword": "Red Light Therapy Device",      "category": "Health & Wellness"},
    {"keyword": "PEMF Mat Device",               "category": "Health & Wellness"},
    {"keyword": "Continuous Glucose Monitor",    "category": "Health & Wellness"},
    {"keyword": "Biological Age Test Kit",       "category": "Health & Wellness"},
    {"keyword": "Microbiome Test Kit",           "category": "Health & Wellness"},
    {"keyword": "Food Sensitivity Test Kit",     "category": "Health & Wellness"},
    {"keyword": "Heavy Metal Test Kit",          "category": "Health & Wellness"},
    {"keyword": "Hormone Test Kit Home",         "category": "Health & Wellness"},
    {"keyword": "HRV Monitor Wearable",          "category": "Health & Wellness"},
    {"keyword": "Oura Ring Alternative",         "category": "Health & Wellness"},
    {"keyword": "Ashwagandha Gummies",           "category": "Health & Wellness"},
    {"keyword": "Coenzyme Q10 Supplement",       "category": "Health & Wellness"},
    {"keyword": "Omega 3 Supplement",            "category": "Health & Wellness"},
    {"keyword": "Sea Buckthorn Supplement",      "category": "Health & Wellness"},
    {"keyword": "Taurine Supplement",            "category": "Health & Wellness"},
    {"keyword": "Creatine Monohydrate",          "category": "Health & Wellness"},

    # ── Beauty (40) ───────────────────────────────────────────────────────────
    {"keyword": "Beef Tallow Skincare",          "category": "Beauty"},
    {"keyword": "Peptide Face Serum",            "category": "Beauty"},
    {"keyword": "Niacinamide Serum",             "category": "Beauty"},
    {"keyword": "Lash Serum",                    "category": "Beauty"},
    {"keyword": "LED Face Mask",                 "category": "Beauty"},
    {"keyword": "Retinol Alternative",           "category": "Beauty"},
    {"keyword": "Snail Mucin Serum",             "category": "Beauty"},
    {"keyword": "Slugging Skincare",             "category": "Beauty"},
    {"keyword": "Facial Gua Sha",                "category": "Beauty"},
    {"keyword": "Ice Roller Face",               "category": "Beauty"},
    {"keyword": "Lip Filler Alternative",        "category": "Beauty"},
    {"keyword": "Glass Skin Routine",            "category": "Beauty"},
    {"keyword": "Body Sunscreen SPF",            "category": "Beauty"},
    {"keyword": "Scalp Serum Hair Growth",       "category": "Beauty"},
    {"keyword": "Rosemary Oil Hair Growth",      "category": "Beauty"},
    {"keyword": "Hair Gloss Treatment",          "category": "Beauty"},
    {"keyword": "Barrier Repair Moisturizer",    "category": "Beauty"},
    {"keyword": "Blue Light Glasses",            "category": "Beauty"},
    {"keyword": "Microcurrent Face Device",      "category": "Beauty"},
    {"keyword": "RF Skin Tightening Device",     "category": "Beauty"},
    {"keyword": "Vitamin C Serum Face",          "category": "Beauty"},
    {"keyword": "Hyaluronic Acid Serum",         "category": "Beauty"},
    {"keyword": "Bakuchiol Serum",               "category": "Beauty"},
    {"keyword": "Azelaic Acid Cream",            "category": "Beauty"},
    {"keyword": "Tranexamic Acid Serum",         "category": "Beauty"},
    {"keyword": "Squalane Oil Skincare",         "category": "Beauty"},
    {"keyword": "Ceramide Moisturizer",          "category": "Beauty"},
    {"keyword": "Centella Asiatica Cream",       "category": "Beauty"},
    {"keyword": "Glycolic Acid Toner",           "category": "Beauty"},
    {"keyword": "Salicylic Acid Cleanser",       "category": "Beauty"},
    {"keyword": "Microneedling Roller Home",     "category": "Beauty"},
    {"keyword": "At Home Laser Hair Removal",    "category": "Beauty"},
    {"keyword": "Dermaplaning Tool",             "category": "Beauty"},
    {"keyword": "Pore Vacuum Blackhead",         "category": "Beauty"},
    {"keyword": "Caffeine Eye Serum",            "category": "Beauty"},
    {"keyword": "Minoxidil Serum Hair",          "category": "Beauty"},
    {"keyword": "DHT Blocker Shampoo",           "category": "Beauty"},
    {"keyword": "Biotin Hair Supplement",        "category": "Beauty"},
    {"keyword": "Teeth Whitening Kit",           "category": "Beauty"},
    {"keyword": "Probiotic Skincare",            "category": "Beauty"},

    # ── Fitness (40) ─────────────────────────────────────────────────────────
    {"keyword": "Portable Sauna Blanket",        "category": "Fitness"},
    {"keyword": "Portable Blender",              "category": "Fitness"},
    {"keyword": "Barefoot Running Shoes",        "category": "Fitness"},
    {"keyword": "Weighted Vest",                 "category": "Fitness"},
    {"keyword": "Cold Plunge Tub",               "category": "Fitness"},
    {"keyword": "Sauna Tent",                    "category": "Fitness"},
    {"keyword": "Walking Pad Treadmill",         "category": "Fitness"},
    {"keyword": "Pull Up Bar Doorframe",         "category": "Fitness"},
    {"keyword": "Resistance Band Set",           "category": "Fitness"},
    {"keyword": "Massage Gun",                   "category": "Fitness"},
    {"keyword": "Incline Treadmill Walking",     "category": "Fitness"},
    {"keyword": "Pilates Reformer Home",         "category": "Fitness"},
    {"keyword": "Rucking Backpack",              "category": "Fitness"},
    {"keyword": "Battle Rope",                   "category": "Fitness"},
    {"keyword": "Adjustable Dumbbell Set",       "category": "Fitness"},
    {"keyword": "Gymnastic Rings",               "category": "Fitness"},
    {"keyword": "Vibration Plate",               "category": "Fitness"},
    {"keyword": "Foam Roller Electric",          "category": "Fitness"},
    {"keyword": "Power Rack Home Gym",           "category": "Fitness"},
    {"keyword": "Cable Machine Home",            "category": "Fitness"},
    {"keyword": "Kettlebell Set",                "category": "Fitness"},
    {"keyword": "Sandbag Training Bag",          "category": "Fitness"},
    {"keyword": "Slant Board Squats",            "category": "Fitness"},
    {"keyword": "Nordic Hamstring Curl Device",  "category": "Fitness"},
    {"keyword": "Ab Roller Wheel",               "category": "Fitness"},
    {"keyword": "Balance Board Fitness",         "category": "Fitness"},
    {"keyword": "Gymnastics Mat",                "category": "Fitness"},
    {"keyword": "Acupressure Mat",               "category": "Fitness"},
    {"keyword": "Compression Boots Recovery",    "category": "Fitness"},
    {"keyword": "Cold Therapy Machine",          "category": "Fitness"},
    {"keyword": "Cupping Therapy Set",           "category": "Fitness"},
    {"keyword": "Grip Strength Trainer",         "category": "Fitness"},
    {"keyword": "Zone 2 Training Monitor",       "category": "Fitness"},
    {"keyword": "Whoop Band Alternative",        "category": "Fitness"},
    {"keyword": "Plant Based Protein Powder",    "category": "Fitness"},
    {"keyword": "Collagen Protein Powder",       "category": "Fitness"},
    {"keyword": "Pre Workout Supplement",        "category": "Fitness"},
    {"keyword": "BCAA Supplement",               "category": "Fitness"},
    {"keyword": "Beta Alanine Supplement",       "category": "Fitness"},
    {"keyword": "Red Light Therapy Panel",       "category": "Fitness"},

    # ── Tech & Gadgets (45) ───────────────────────────────────────────────────
    {"keyword": "AI Smart Ring",                 "category": "Tech"},
    {"keyword": "Mini Projector",                "category": "Tech"},
    {"keyword": "Portable Power Station",        "category": "Tech"},
    {"keyword": "Solar Panel Charger",           "category": "Tech"},
    {"keyword": "Smart Home Hub",                "category": "Tech"},
    {"keyword": "Robot Vacuum Mop Combo",        "category": "Tech"},
    {"keyword": "Air Quality Monitor",           "category": "Tech"},
    {"keyword": "Wireless Earbuds",              "category": "Tech"},
    {"keyword": "Dashcam 4K",                    "category": "Tech"},
    {"keyword": "Action Camera",                 "category": "Tech"},
    {"keyword": "Smart Glasses",                 "category": "Tech"},
    {"keyword": "Portable Monitor",              "category": "Tech"},
    {"keyword": "Mechanical Keyboard",           "category": "Tech"},
    {"keyword": "Magnetic Phone Mount",          "category": "Tech"},
    {"keyword": "E Ink Tablet",                  "category": "Tech"},
    {"keyword": "Satellite Communicator",        "category": "Tech"},
    {"keyword": "Bone Conduction Headphones",    "category": "Tech"},
    {"keyword": "Open Ear Headphones",           "category": "Tech"},
    {"keyword": "Sleep Headphones",              "category": "Tech"},
    {"keyword": "Noise Canceling Earplugs",      "category": "Tech"},
    {"keyword": "USB C Hub Multiport",           "category": "Tech"},
    {"keyword": "Thunderbolt Dock",              "category": "Tech"},
    {"keyword": "Capture Card 4K Streaming",     "category": "Tech"},
    {"keyword": "Teleprompter Phone",            "category": "Tech"},
    {"keyword": "Ring Light Professional",       "category": "Tech"},
    {"keyword": "Podcast Microphone USB",        "category": "Tech"},
    {"keyword": "Lavalier Microphone Wireless",  "category": "Tech"},
    {"keyword": "Smart Plug Energy Monitor",     "category": "Tech"},
    {"keyword": "Robot Lawnmower",               "category": "Tech"},
    {"keyword": "Electric Bike Conversion Kit",  "category": "Tech"},
    {"keyword": "E Scooter Commuter",            "category": "Tech"},
    {"keyword": "Handheld Game Console",         "category": "Tech"},
    {"keyword": "Portable Gaming PC",            "category": "Tech"},
    {"keyword": "AI Meeting Recorder",           "category": "Tech"},
    {"keyword": "Pocket AI Device",              "category": "Tech"},
    {"keyword": "Water Purifier Countertop",     "category": "Tech"},
    {"keyword": "Air Purifier Smart",            "category": "Tech"},
    {"keyword": "EMF Meter",                     "category": "Tech"},
    {"keyword": "Thermal Camera Smartphone",     "category": "Tech"},
    {"keyword": "Stream Deck Alternative",       "category": "Tech"},
    {"keyword": "KVM Switch Monitor",            "category": "Tech"},
    {"keyword": "Standing Desk Electric",        "category": "Tech"},
    {"keyword": "Monitor Light Bar",             "category": "Tech"},
    {"keyword": "Webcam 4K Streaming",           "category": "Tech"},
    {"keyword": "Smart Lock Door",               "category": "Tech"},

    # ── Home & Kitchen (45) ───────────────────────────────────────────────────
    {"keyword": "Water Bottle with Filter",      "category": "Home & Kitchen"},
    {"keyword": "Freeze Dryer Home",             "category": "Home & Kitchen"},
    {"keyword": "Air Fryer Accessories",         "category": "Home & Kitchen"},
    {"keyword": "Countertop Dishwasher",         "category": "Home & Kitchen"},
    {"keyword": "Sous Vide Machine",             "category": "Home & Kitchen"},
    {"keyword": "Dutch Oven Cast Iron",          "category": "Home & Kitchen"},
    {"keyword": "Bread Maker Machine",           "category": "Home & Kitchen"},
    {"keyword": "Espresso Machine Home",         "category": "Home & Kitchen"},
    {"keyword": "Mushroom Growing Kit",          "category": "Home & Kitchen"},
    {"keyword": "Compost Bin Kitchen",           "category": "Home & Kitchen"},
    {"keyword": "Fermentation Crock",            "category": "Home & Kitchen"},
    {"keyword": "Dehydrator Machine",            "category": "Home & Kitchen"},
    {"keyword": "Reusable Produce Bags",         "category": "Home & Kitchen"},
    {"keyword": "Smart Thermostat",              "category": "Home & Kitchen"},
    {"keyword": "Cordless Vacuum",               "category": "Home & Kitchen"},
    {"keyword": "Indoor Garden Hydroponics",     "category": "Home & Kitchen"},
    {"keyword": "Microgreens Growing Kit",       "category": "Home & Kitchen"},
    {"keyword": "Sourdough Starter Kit",         "category": "Home & Kitchen"},
    {"keyword": "Pasta Maker Electric",          "category": "Home & Kitchen"},
    {"keyword": "Ice Cream Maker",               "category": "Home & Kitchen"},
    {"keyword": "Vacuum Sealer Machine",         "category": "Home & Kitchen"},
    {"keyword": "Yogurt Maker",                  "category": "Home & Kitchen"},
    {"keyword": "Kombucha Brewing Kit",          "category": "Home & Kitchen"},
    {"keyword": "Carbon Steel Pan",              "category": "Home & Kitchen"},
    {"keyword": "Wok Carbon Steel",              "category": "Home & Kitchen"},
    {"keyword": "Pizza Steel Baking",            "category": "Home & Kitchen"},
    {"keyword": "Outdoor Pizza Oven",            "category": "Home & Kitchen"},
    {"keyword": "Flat Top Grill Griddle",        "category": "Home & Kitchen"},
    {"keyword": "Pellet Smoker Grill",           "category": "Home & Kitchen"},
    {"keyword": "Kamado Grill",                  "category": "Home & Kitchen"},
    {"keyword": "Pressure Canner",               "category": "Home & Kitchen"},
    {"keyword": "Water Bath Canning Kit",        "category": "Home & Kitchen"},
    {"keyword": "Bread Proofing Basket",         "category": "Home & Kitchen"},
    {"keyword": "Grain Mill Home",               "category": "Home & Kitchen"},
    {"keyword": "Jerky Maker Machine",           "category": "Home & Kitchen"},
    {"keyword": "Mandoline Slicer",              "category": "Home & Kitchen"},
    {"keyword": "Tortilla Press",                "category": "Home & Kitchen"},
    {"keyword": "Clay Cooking Pot",              "category": "Home & Kitchen"},
    {"keyword": "Fire Pit Cooking Grate",        "category": "Home & Kitchen"},
    {"keyword": "Dutch Baby Pan",                "category": "Home & Kitchen"},
    {"keyword": "Crepe Maker Electric",          "category": "Home & Kitchen"},
    {"keyword": "Waffle Maker Belgian",          "category": "Home & Kitchen"},
    {"keyword": "Robot Mop Smart",               "category": "Home & Kitchen"},
    {"keyword": "Window Cleaning Robot",         "category": "Home & Kitchen"},
    {"keyword": "Pool Cleaning Robot",           "category": "Home & Kitchen"},

    # ── Pets (30) ─────────────────────────────────────────────────────────────
    {"keyword": "Dog Probiotic Chews",           "category": "Pets"},
    {"keyword": "Cat Water Fountain",            "category": "Pets"},
    {"keyword": "Raw Dog Food",                  "category": "Pets"},
    {"keyword": "Dog Anxiety Vest",              "category": "Pets"},
    {"keyword": "Cat GPS Tracker",               "category": "Pets"},
    {"keyword": "Automatic Cat Feeder",          "category": "Pets"},
    {"keyword": "Dog DNA Test Kit",              "category": "Pets"},
    {"keyword": "Pet Camera Treat Dispenser",    "category": "Pets"},
    {"keyword": "Freeze Dried Dog Food",         "category": "Pets"},
    {"keyword": "Orthopedic Dog Bed",            "category": "Pets"},
    {"keyword": "Dog Joint Supplement",          "category": "Pets"},
    {"keyword": "Dog Omega 3 Supplement",        "category": "Pets"},
    {"keyword": "Cat Dental Chews",              "category": "Pets"},
    {"keyword": "Cat Probiotic Supplement",      "category": "Pets"},
    {"keyword": "Dog CBD Oil",                   "category": "Pets"},
    {"keyword": "Pet Stroller",                  "category": "Pets"},
    {"keyword": "Dog Backpack Carrier",          "category": "Pets"},
    {"keyword": "Cat Backpack Carrier",          "category": "Pets"},
    {"keyword": "Dog Cooling Mat",               "category": "Pets"},
    {"keyword": "Dog Life Jacket",               "category": "Pets"},
    {"keyword": "Automatic Litter Box",          "category": "Pets"},
    {"keyword": "Bird Feeder Camera",            "category": "Pets"},
    {"keyword": "Aquarium LED Light",            "category": "Pets"},
    {"keyword": "Reptile UVB Light",             "category": "Pets"},
    {"keyword": "Chicken Coop Backyard",         "category": "Pets"},
    {"keyword": "Beekeeping Starter Kit",        "category": "Pets"},
    {"keyword": "Cat Grass Kit",                 "category": "Pets"},
    {"keyword": "Dog Mobility Harness",          "category": "Pets"},
    {"keyword": "Pet GPS Tracker Collar",        "category": "Pets"},
    {"keyword": "Dog Treadmill",                 "category": "Pets"},

    # ── Fashion & Apparel (30) ────────────────────────────────────────────────
    {"keyword": "Linen Clothing",                "category": "Fashion"},
    {"keyword": "Merino Wool Base Layer",        "category": "Fashion"},
    {"keyword": "Wide Leg Pants",                "category": "Fashion"},
    {"keyword": "Bamboo Pajamas",                "category": "Fashion"},
    {"keyword": "Tactical Pants",                "category": "Fashion"},
    {"keyword": "Minimalist Sneakers",           "category": "Fashion"},
    {"keyword": "Crossbody Bag",                 "category": "Fashion"},
    {"keyword": "Bucket Hat UV Protection",      "category": "Fashion"},
    {"keyword": "Swim Shorts Quick Dry",         "category": "Fashion"},
    {"keyword": "Merino Wool Socks",             "category": "Fashion"},
    {"keyword": "Compression Socks Performance", "category": "Fashion"},
    {"keyword": "Posture Corrector Brace",       "category": "Fashion"},
    {"keyword": "Heated Vest",                   "category": "Fashion"},
    {"keyword": "Cooling Vest",                  "category": "Fashion"},
    {"keyword": "UV Protection Clothing",        "category": "Fashion"},
    {"keyword": "Rash Guard SPF Shirt",          "category": "Fashion"},
    {"keyword": "Trail Running Shorts",          "category": "Fashion"},
    {"keyword": "Running Vest Hydration",        "category": "Fashion"},
    {"keyword": "Hiking Pants Convertible",      "category": "Fashion"},
    {"keyword": "Fleece Lined Leggings",         "category": "Fashion"},
    {"keyword": "Down Jacket Packable",          "category": "Fashion"},
    {"keyword": "Rain Jacket Packable",          "category": "Fashion"},
    {"keyword": "Windbreaker Ultralight",        "category": "Fashion"},
    {"keyword": "Trail Running Shoes",           "category": "Fashion"},
    {"keyword": "Barefoot Shoes",                "category": "Fashion"},
    {"keyword": "Zero Drop Shoes",               "category": "Fashion"},
    {"keyword": "Chelsea Boots",                 "category": "Fashion"},
    {"keyword": "Platform Sneakers",             "category": "Fashion"},
    {"keyword": "Puffer Vest",                   "category": "Fashion"},
    {"keyword": "Trucker Hat Mesh",              "category": "Fashion"},

    # ── Baby & Kids (25) ─────────────────────────────────────────────────────
    {"keyword": "Baby Carrier Ergonomic",        "category": "Baby & Kids"},
    {"keyword": "Convertible Car Seat",          "category": "Baby & Kids"},
    {"keyword": "Baby Sound Machine",            "category": "Baby & Kids"},
    {"keyword": "Baby Monitor AI",               "category": "Baby & Kids"},
    {"keyword": "Baby Food Maker",               "category": "Baby & Kids"},
    {"keyword": "Silicone Baby Bib",             "category": "Baby & Kids"},
    {"keyword": "Montessori Toy Set",            "category": "Baby & Kids"},
    {"keyword": "Sensory Play Kit",              "category": "Baby & Kids"},
    {"keyword": "Busy Board Toddler",            "category": "Baby & Kids"},
    {"keyword": "Learning Tower Kitchen Kids",   "category": "Baby & Kids"},
    {"keyword": "Balance Bike Kids",             "category": "Baby & Kids"},
    {"keyword": "Wobble Board Kids",             "category": "Baby & Kids"},
    {"keyword": "Trampoline Indoor Kids",        "category": "Baby & Kids"},
    {"keyword": "Climbing Frame Indoor",         "category": "Baby & Kids"},
    {"keyword": "Kinetic Sand Alternative",      "category": "Baby & Kids"},
    {"keyword": "Magnetic Drawing Board",        "category": "Baby & Kids"},
    {"keyword": "Audiobook Player Kids",         "category": "Baby & Kids"},
    {"keyword": "Smartwatch Kids GPS",           "category": "Baby & Kids"},
    {"keyword": "Diaper Bag Backpack",           "category": "Baby & Kids"},
    {"keyword": "Stroller Compact Fold",         "category": "Baby & Kids"},
    {"keyword": "Toddler Snack Container",       "category": "Baby & Kids"},
    {"keyword": "White Noise Machine Baby",      "category": "Baby & Kids"},
    {"keyword": "Baby Swing Electric",           "category": "Baby & Kids"},
    {"keyword": "Nursing Pillow",                "category": "Baby & Kids"},
    {"keyword": "Toddler Toothbrush Electric",   "category": "Baby & Kids"},

    # ── Outdoor & Adventure (25) ──────────────────────────────────────────────
    {"keyword": "Packable Hammock",              "category": "Outdoor"},
    {"keyword": "Ultralight Sleeping Bag",       "category": "Outdoor"},
    {"keyword": "Bivy Sack",                     "category": "Outdoor"},
    {"keyword": "Trekking Poles Carbon",         "category": "Outdoor"},
    {"keyword": "Hiking Water Filter",           "category": "Outdoor"},
    {"keyword": "Gravity Water Filter",          "category": "Outdoor"},
    {"keyword": "Solar Shower Bag",              "category": "Outdoor"},
    {"keyword": "Backpacking Stove",             "category": "Outdoor"},
    {"keyword": "Bear Canister",                 "category": "Outdoor"},
    {"keyword": "Headlamp Rechargeable",         "category": "Outdoor"},
    {"keyword": "Emergency Bivouac Bag",         "category": "Outdoor"},
    {"keyword": "Fire Starter Kit",              "category": "Outdoor"},
    {"keyword": "Portable Power Bank Solar",     "category": "Outdoor"},
    {"keyword": "Hiking Boot Waterproof",        "category": "Outdoor"},
    {"keyword": "Hydration Pack Backpack",       "category": "Outdoor"},
    {"keyword": "Tarp Shelter Ultralight",       "category": "Outdoor"},
    {"keyword": "Orienteering Compass",          "category": "Outdoor"},
    {"keyword": "Altimeter Watch",               "category": "Outdoor"},
    {"keyword": "Survival Bracelet",             "category": "Outdoor"},
    {"keyword": "Kayak Inflatable",              "category": "Outdoor"},
    {"keyword": "Stand Up Paddle Board",         "category": "Outdoor"},
    {"keyword": "Snorkel Set",                   "category": "Outdoor"},
    {"keyword": "Rock Climbing Shoes",           "category": "Outdoor"},
    {"keyword": "Fly Fishing Starter Kit",       "category": "Outdoor"},
    {"keyword": "Overlanding Gear",              "category": "Outdoor"},

    # ── Sleep & Recovery (20) ─────────────────────────────────────────────────
    {"keyword": "Cooling Mattress Topper",       "category": "Sleep"},
    {"keyword": "Weighted Blanket",              "category": "Sleep"},
    {"keyword": "Silk Sleep Mask",               "category": "Sleep"},
    {"keyword": "Nasal Dilator Sleep",           "category": "Sleep"},
    {"keyword": "Anti Snoring Mouthpiece",       "category": "Sleep"},
    {"keyword": "Melatonin Gummies",             "category": "Sleep"},
    {"keyword": "Magnesium Glycinate Sleep",     "category": "Sleep"},
    {"keyword": "L-Theanine Supplement",         "category": "Sleep"},
    {"keyword": "Ashwagandha Sleep Supplement",  "category": "Sleep"},
    {"keyword": "GABA Supplement Sleep",         "category": "Sleep"},
    {"keyword": "Phosphatidylserine Supplement", "category": "Sleep"},
    {"keyword": "Sunrise Alarm Clock",           "category": "Sleep"},
    {"keyword": "Sleep Tracking Ring",           "category": "Sleep"},
    {"keyword": "Chilipad Alternative",          "category": "Sleep"},
    {"keyword": "White Noise Machine",           "category": "Sleep"},
    {"keyword": "Earthing Sheet Grounding",      "category": "Sleep"},
    {"keyword": "Blackout Curtains",             "category": "Sleep"},
    {"keyword": "Sleep Apnea Chinstrap",         "category": "Sleep"},
    {"keyword": "Mouth Guard Night",             "category": "Sleep"},
    {"keyword": "Chronotype Test",               "category": "Sleep"},

    # ── Coffee & Beverages (20) ───────────────────────────────────────────────
    {"keyword": "Pour Over Coffee Set",          "category": "Coffee & Beverages"},
    {"keyword": "Aeropress Coffee Maker",        "category": "Coffee & Beverages"},
    {"keyword": "Moka Pot Espresso",             "category": "Coffee & Beverages"},
    {"keyword": "Cold Brew Coffee Maker",        "category": "Coffee & Beverages"},
    {"keyword": "Nitro Cold Brew Kit",           "category": "Coffee & Beverages"},
    {"keyword": "Espresso Puck Screen",          "category": "Coffee & Beverages"},
    {"keyword": "WDT Tool Coffee",               "category": "Coffee & Beverages"},
    {"keyword": "Coffee Scale Precision",        "category": "Coffee & Beverages"},
    {"keyword": "Burr Grinder Electric",         "category": "Coffee & Beverages"},
    {"keyword": "Hand Grinder Coffee",           "category": "Coffee & Beverages"},
    {"keyword": "Matcha Whisk Set",              "category": "Coffee & Beverages"},
    {"keyword": "Ceremonial Grade Matcha",       "category": "Coffee & Beverages"},
    {"keyword": "Mushroom Coffee Mix",           "category": "Coffee & Beverages"},
    {"keyword": "Adaptogen Coffee",              "category": "Coffee & Beverages"},
    {"keyword": "Chaga Tea",                     "category": "Coffee & Beverages"},
    {"keyword": "Barley Tea",                    "category": "Coffee & Beverages"},
    {"keyword": "Hojicha Powder",                "category": "Coffee & Beverages"},
    {"keyword": "Butter Coffee Kit",             "category": "Coffee & Beverages"},
    {"keyword": "French Press Insulated",        "category": "Coffee & Beverages"},
    {"keyword": "Milk Frother Electric",         "category": "Coffee & Beverages"},

    # ── Sustainability (20) ───────────────────────────────────────────────────
    {"keyword": "Silicone Food Storage Bags",    "category": "Sustainability"},
    {"keyword": "Reusable Cotton Rounds",        "category": "Sustainability"},
    {"keyword": "Bamboo Toothbrush",             "category": "Sustainability"},
    {"keyword": "Compostable Trash Bags",        "category": "Sustainability"},
    {"keyword": "Wool Dryer Balls",              "category": "Sustainability"},
    {"keyword": "Shampoo Bar",                   "category": "Sustainability"},
    {"keyword": "Solid Conditioner Bar",         "category": "Sustainability"},
    {"keyword": "Zero Waste Starter Kit",        "category": "Sustainability"},
    {"keyword": "Menstrual Cup",                 "category": "Sustainability"},
    {"keyword": "Period Underwear",              "category": "Sustainability"},
    {"keyword": "Bamboo Paper Towel",            "category": "Sustainability"},
    {"keyword": "Swedish Dishcloth",             "category": "Sustainability"},
    {"keyword": "Natural Loofah",                "category": "Sustainability"},
    {"keyword": "Beeswax Candle",                "category": "Sustainability"},
    {"keyword": "Soy Candle Making Kit",         "category": "Sustainability"},
    {"keyword": "Package Free Deodorant",        "category": "Sustainability"},
    {"keyword": "Refillable Water Bottle",       "category": "Sustainability"},
    {"keyword": "Biodegradable Glitter",         "category": "Sustainability"},
    {"keyword": "Reusable Straw Set",            "category": "Sustainability"},
    {"keyword": "Beeswax Wrap",                  "category": "Sustainability"},

    # ── Mental Health & Focus (20) ────────────────────────────────────────────
    {"keyword": "Nootropic Supplement",          "category": "Mental Health"},
    {"keyword": "Alpha GPC Supplement",          "category": "Mental Health"},
    {"keyword": "Bacopa Monnieri Supplement",    "category": "Mental Health"},
    {"keyword": "Rhodiola Rosea Supplement",     "category": "Mental Health"},
    {"keyword": "Panax Ginseng Supplement",      "category": "Mental Health"},
    {"keyword": "Ginkgo Biloba Supplement",      "category": "Mental Health"},
    {"keyword": "L-Tyrosine Supplement",         "category": "Mental Health"},
    {"keyword": "5-HTP Supplement",              "category": "Mental Health"},
    {"keyword": "St John's Wort Supplement",     "category": "Mental Health"},
    {"keyword": "SAMe Supplement",               "category": "Mental Health"},
    {"keyword": "Meditation Headband EEG",       "category": "Mental Health"},
    {"keyword": "Neurofeedback Device Home",     "category": "Mental Health"},
    {"keyword": "Vagus Nerve Stimulator",        "category": "Mental Health"},
    {"keyword": "Biofeedback Device",            "category": "Mental Health"},
    {"keyword": "Journaling Notebook Guided",    "category": "Mental Health"},
    {"keyword": "Light Therapy Lamp SAD",        "category": "Mental Health"},
    {"keyword": "Anxiety Relief Supplement",     "category": "Mental Health"},
    {"keyword": "Stress Relief Supplement",      "category": "Mental Health"},
    {"keyword": "Focus Supplement",              "category": "Mental Health"},
    {"keyword": "Memory Supplement",             "category": "Mental Health"},
]

# Seed terms used to discover new keywords per category
DISCOVERY_SEEDS = {
    "Health & Wellness": ["health supplement", "wellness product", "biohacking"],
    "Beauty":            ["skincare product", "beauty trend", "hair care"],
    "Fitness":           ["fitness equipment", "home gym", "workout gear"],
    "Tech":              ["tech gadget", "smart device", "wearable tech"],
    "Home & Kitchen":    ["kitchen gadget", "home product", "cooking tool"],
    "Pets":              ["pet product", "dog accessory", "cat product"],
    "Fashion":           ["fashion trend", "clothing style", "accessories"],
    "Baby & Kids":       ["baby product", "toddler toy", "kids gadget"],
    "Outdoor":           ["camping gear", "outdoor adventure", "hiking gear"],
    "Sleep":             ["sleep supplement", "sleep tracker", "sleep aid"],
    "Coffee & Beverages":["coffee gear", "matcha product", "mushroom drink"],
    "Sustainability":    ["zero waste product", "eco friendly", "sustainable living"],
    "Mental Health":     ["nootropic supplement", "focus supplement", "stress relief"],
}

# Words that indicate a query is not a product (filter these out)
NON_PRODUCT_PATTERNS = re.compile(
    r"\b(how to|what is|where to|why|who|when|best|review|vs|versus|near me|"
    r"recipe|tutorial|ideas|tips|guide|meaning|definition|price)\b",
    re.IGNORECASE,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Keyword list management ───────────────────────────────────────────────────

def load_keywords() -> list[dict]:
    """Load keywords.json, or fall back to defaults."""
    if KEYWORDS_FILE.exists():
        try:
            data = json.loads(KEYWORDS_FILE.read_text())
            log.info("Loaded %d keywords from %s", len(data), KEYWORDS_FILE)
            return data
        except Exception as e:
            log.warning("Could not read keywords.json (%s) — using defaults", e)
    log.info("No keywords.json found — using default list (%d keywords)", len(DEFAULT_KEYWORDS))
    return [dict(k, status="active", added=datetime.utcnow().date().isoformat())
            for k in DEFAULT_KEYWORDS]


def save_keywords(keywords: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    KEYWORDS_FILE.write_text(json.dumps(keywords, indent=2))
    log.info("Saved %d keywords to %s", len(keywords), KEYWORDS_FILE)


# ── Trend analysis ────────────────────────────────────────────────────────────

def compute_growth(series: list[float]) -> float:
    non_zero = [v for v in series if v > 0]
    if len(non_zero) < 2:
        return 0.0
    return round(((series[-1] - non_zero[0]) / non_zero[0]) * 100, 1)


def trend_score(series: list[float], growth: float) -> int:
    if not series:
        return 0
    recency  = series[-1]
    momentum = min(growth / 50, 100)
    return min(100, max(0, round(0.6 * recency + 0.4 * momentum)))


def classify_momentum(growth: float) -> str:
    if growth >= 1000:
        return "breakout"
    if growth >= 200:
        return "hot"
    return "rising"


def compute_viability(
    growth: float,
    series: list[float],
    status: str,
    reddit_30d: int,
    reddit_velocity: float | None,
    amazon_result_count: int | None,
    amazon_avg_price: float | None,
    amazon_avg_rating: float | None,
    amazon_top_reviews: int | None,
    amazon_best_seller: bool,
    amazons_choice: bool,
) -> tuple[int, dict]:
    """
    Product viability score (1–100) for entrepreneurs evaluating a market.

    Five factors (max points shown):
      1. Trend Momentum   (30 pts) — Is demand growing? At what stage?
      2. Current Interest (20 pts) — How strong is interest RIGHT NOW?
      3. Competition      (25 pts) — How crowded is Amazon? Fewer = easier entry.
      4. Price Viability  (10 pts) — Is the avg price high enough for margin?
      5. Social Demand    (15 pts) — Reddit community validation + velocity.

    Bonuses/penalties:
      • Best Seller or Amazon's Choice badge   +3  (proven buyer demand)
      • Strong reviews on top product          +2  (market validated)
      • Flat/declining status                  hard cap at 30
    """
    breakdown = {}

    # ── 1. Trend Momentum (0–30) ───────────────────────────────────────────────
    # Sweet spot is Hot (200–999%): proven demand but not yet saturated.
    # Breakout is exciting but risky (early, unproven longevity) → slight penalty.
    if status == "flat":
        m = 0
    elif growth >= 2000:
        m = 24   # extreme breakout — huge risk, could be a fad
    elif growth >= 1000:
        m = 27   # breakout — exciting but volatile
    elif growth >= 500:
        m = 30   # hot sweet spot
    elif growth >= 200:
        m = 28
    elif growth >= 100:
        m = 22
    elif growth >= 50:
        m = 16
    elif growth > 0:
        m = 10
    else:
        m = 3
    breakdown["trend_momentum"] = m

    # ── 2. Current Interest (0–20) ─────────────────────────────────────────────
    # Last month's Google Trends index (0–100) scaled to 0–20.
    current = series[-1] if series else 0
    i = round(current / 100 * 20)
    breakdown["current_interest"] = i

    # ── 3. Competition (0–25) ──────────────────────────────────────────────────
    # Fewer Amazon listings = easier to rank and stand out.
    if amazon_result_count is None:
        c = 12   # no data → neutral
    elif amazon_result_count < 100:
        c = 25   # near-blue-ocean
    elif amazon_result_count < 500:
        c = 22
    elif amazon_result_count < 2_000:
        c = 17
    elif amazon_result_count < 5_000:
        c = 12
    elif amazon_result_count < 15_000:
        c = 7
    else:
        c = 3    # very crowded
    breakdown["competition"] = c

    # ── 4. Price Viability (0–10) ──────────────────────────────────────────────
    # Higher avg price = more margin room for a new entrant.
    if amazon_avg_price is None:
        p = 5    # neutral
    elif amazon_avg_price >= 100:
        p = 10
    elif amazon_avg_price >= 60:
        p = 9
    elif amazon_avg_price >= 35:
        p = 7
    elif amazon_avg_price >= 20:
        p = 4
    else:
        p = 1    # race-to-bottom pricing
    breakdown["price_viability"] = p

    # ── 5. Social Demand (0–15) ────────────────────────────────────────────────
    # Reddit mentions = organic community interest (not paid/manufactured).
    r30 = reddit_30d or 0
    if r30 >= 100:
        s = 14
    elif r30 >= 50:
        s = 12
    elif r30 >= 20:
        s = 10
    elif r30 >= 10:
        s = 7
    elif r30 >= 3:
        s = 4
    else:
        s = 1
    # Velocity bonus: community buzz is accelerating
    if reddit_velocity is not None and reddit_velocity >= 50:
        s = min(15, s + 2)
    breakdown["social_demand"] = s

    # ── Bonuses ────────────────────────────────────────────────────────────────
    bonus = 0
    if amazon_best_seller or amazons_choice:
        bonus += 3   # proven buyer demand exists in this category
    if (amazon_top_reviews or 0) >= 1000:
        bonus += 2   # market is validated — customers actively buying
    # Slight penalty for very poor ratings (quality gap may be hard to overcome)
    if amazon_avg_rating is not None and amazon_avg_rating < 3.5:
        bonus -= 3
    breakdown["bonus"] = bonus

    raw = m + i + c + p + s + bonus

    # ── Flat hard cap ──────────────────────────────────────────────────────────
    if status == "flat":
        raw = min(raw, 30)

    score = max(1, min(100, raw))
    return score, breakdown


def is_flat(series: list[float]) -> bool:
    """
    Returns True if the keyword's interest has peaked and is meaningfully
    declining — not just seasonal noise.

    Criteria:
      1. Peak was significant (reached FADING_PEAK_MIN at some point)
      2. Recent 3-month average is below FADING_RECENT_RATIO of the peak
      3. The last FADING_SLOPE_WINDOW months show a declining slope
    """
    if len(series) < 6:
        return False
    peak = max(series)
    if peak < FADING_PEAK_MIN:
        return False  # Was never notable enough to call it "flat"

    recent     = series[-3:]
    recent_avg = sum(recent) / len(recent)
    if recent_avg >= peak * FADING_RECENT_RATIO:
        return False  # Still healthy relative to peak

    # Check slope of last N months is negative
    window = series[-FADING_SLOPE_WINDOW:]
    slope  = window[-1] - window[0]
    return slope < 0


# ── Google Trends fetching ────────────────────────────────────────────────────

def fetch_batch(pytrends: TrendReq, batch: list[str]) -> dict:
    results = {}
    try:
        pytrends.build_payload(batch, timeframe=TIMEFRAME, geo=GEO)
        df = pytrends.interest_over_time()
        if df.empty:
            return results
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        try:
            monthly = df.resample("ME").mean().tail(12)
        except Exception:
            monthly = df.resample("M").mean().tail(12)
        col_map = {c.lower(): c for c in monthly.columns}
        for kw in batch:
            col = col_map.get(kw.lower())
            if col:
                series = monthly[col].fillna(0).tolist()
                results[kw] = [round(v, 1) for v in series]
                log.info("  ✓ %s", kw)
            else:
                log.warning("  ✗ %s (not in response)", kw)
    except Exception as exc:
        if "429" in str(exc):
            log.warning("Rate limited — waiting 30s and retrying")
            time.sleep(30)
            try:
                pytrends.build_payload(batch, timeframe=TIMEFRAME, geo=GEO)
                df = pytrends.interest_over_time()
                if not df.empty:
                    if "isPartial" in df.columns:
                        df = df.drop(columns=["isPartial"])
                    try:
                        monthly = df.resample("ME").mean().tail(12)
                    except Exception:
                        monthly = df.resample("M").mean().tail(12)
                    col_map = {c.lower(): c for c in monthly.columns}
                    for kw in batch:
                        col = col_map.get(kw.lower())
                        if col:
                            results[kw] = [round(v, 1) for v in monthly[col].fillna(0).tolist()]
            except Exception as e2:
                log.warning("Retry failed: %s", e2)
        else:
            log.warning("Batch error: %s", exc)
    return results


def fetch_all_trends(keywords: list[str]) -> dict:
    """Fetch Google Trends for all keywords. Returns {keyword: [monthly series]}."""
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    raw = {}
    batch_size = 5
    batches = [keywords[i:i+batch_size] for i in range(0, len(keywords), batch_size)]
    for i, batch in enumerate(batches, 1):
        log.info("Google Trends batch %d/%d", i, len(batches))
        raw.update(fetch_batch(pytrends, batch))
        if i < len(batches):
            time.sleep(12)
    return raw


# ── Keyword discovery ─────────────────────────────────────────────────────────

def looks_like_product(query: str) -> bool:
    """Heuristic: filter out questions, how-tos, and non-product searches."""
    q = query.strip()
    if len(q) < 4 or len(q) > 60:
        return False
    if NON_PRODUCT_PATTERNS.search(q):
        return False
    # Must contain at least one letter (no pure numbers/symbols)
    if not re.search(r"[a-zA-Z]{3}", q):
        return False
    return True


def discover_new_keywords(existing_keywords: set[str]) -> list[dict]:
    """
    Use pytrends related_queries to find rising product keywords
    not already in our list.
    """
    pytrends    = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    discovered  = []
    seen        = set(k.lower() for k in existing_keywords)

    for category, seeds in DISCOVERY_SEEDS.items():
        for seed in seeds:
            try:
                log.info("Discovering via seed: '%s' (%s)", seed, category)
                pytrends.build_payload([seed], timeframe="today 3-m", geo=GEO)
                related = pytrends.related_queries()
                rising  = related.get(seed, {}).get("rising")
                if rising is None or rising.empty:
                    time.sleep(5)
                    continue

                for _, row in rising.iterrows():
                    query = str(row.get("query", "")).strip()
                    if (query.lower() not in seen
                            and looks_like_product(query)
                            and len(discovered) < MAX_NEW_PER_RUN):
                        discovered.append({
                            "keyword":  query.title(),
                            "category": category,
                            "status":   "active",
                            "added":    datetime.utcnow().date().isoformat(),
                            "is_new":   True,
                        })
                        seen.add(query.lower())
                        log.info("  🆕 Discovered: %s (%s)", query, category)

                time.sleep(8)
            except Exception as exc:
                log.warning("Discovery failed for '%s': %s", seed, exc)
                time.sleep(5)

    log.info("Discovered %d new keywords", len(discovered))
    return discovered


# ── Reddit ────────────────────────────────────────────────────────────────────

def fetch_reddit_mentions(keyword: str) -> tuple[int, int, float | None]:
    now       = datetime.now(timezone.utc)
    week_ago  = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    total_30d = this_week = last_week = 0
    try:
        resp = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": keyword, "sort": "new", "limit": 100, "t": "month", "type": "link"},
            headers=REDDIT_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return 0, 0, None
        for post in resp.json().get("data", {}).get("children", []):
            created = datetime.fromtimestamp(post["data"]["created_utc"], tz=timezone.utc)
            if created >= month_ago:
                total_30d += 1
            if created >= week_ago:
                this_week += 1
            elif created >= week_ago - timedelta(days=7):
                last_week += 1
    except Exception as exc:
        log.warning("Reddit fetch failed for '%s': %s", keyword, exc)
    velocity = None
    if this_week > 0 or last_week > 0:
        velocity = 100.0 if last_week == 0 else round(((this_week - last_week) / last_week) * 100, 1)
    log.info("  Reddit '%s': %d/30d  %d this week", keyword, total_30d, this_week)
    return total_30d, this_week, velocity


# ── Amazon ───────────────────────────────────────────────────────────────────

def _amazon_headers() -> dict:
    return {
        "User-Agent": random.choice(AMAZON_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def fetch_amazon_data(keyword: str) -> dict:
    """
    Scrape Amazon search results for a keyword.
    Returns:
      result_count   – approximate total listings (market size)
      best_seller    – True if any top-10 result has a Best Seller badge
      amazons_choice – True if any top-10 result has an Amazon's Choice badge
      top_reviews    – review count of the highest-reviewed top-10 product
      avg_price      – average price across top-10 priced products (USD)
      avg_rating     – average star rating across top-10 rated products
      seller_count   – number of distinct products on the first page (competition proxy)
    """
    empty = {
        "amazon_result_count": None,
        "amazon_best_seller":  False,
        "amazons_choice":      False,
        "amazon_top_reviews":  None,
        "amazon_avg_price":    None,
        "amazon_avg_rating":   None,
        "amazon_seller_count": None,
    }
    url = f"https://www.amazon.com/s?k={quote_plus(keyword)}&ref=nb_sb_noss"
    try:
        resp = AMAZON_SESSION.get(url, headers=_amazon_headers(), timeout=15)
        if resp.status_code != 200:
            log.warning("  Amazon HTTP %d for '%s'", resp.status_code, keyword)
            return empty
        # Detect CAPTCHA / robot-check page
        if "robot" in resp.text[:2000].lower() or "captcha" in resp.text[:2000].lower():
            log.warning("  Amazon bot-check triggered for '%s' — skipping", keyword)
            return empty

        soup = BeautifulSoup(resp.text, "lxml")

        # ── Result count ──────────────────────────────────────────────────────
        result_count = None
        count_el = soup.select_one("span.a-color-state.a-text-bold, span[data-component-type='s-result-info-bar'] h1")
        if not count_el:
            # try the breadcrumb-style count
            for span in soup.find_all("span", class_="a-color-state"):
                txt = span.get_text(" ", strip=True)
                if "result" in txt.lower():
                    count_el = span
                    break
        if count_el:
            txt = count_el.get_text(" ", strip=True)
            # e.g. "1-16 of over 4,000 results" or "over 1,000 results"
            nums = re.findall(r"[\d,]+", txt.replace(",", ""))
            if nums:
                result_count = int(max(nums, key=lambda n: int(n)))

        # ── Product cards ─────────────────────────────────────────────────────
        cards = soup.select("div[data-component-type='s-search-result']")[:10]

        best_seller  = False
        amazons_choice = False
        prices       = []
        ratings      = []
        review_counts = []

        for card in cards:
            text = card.get_text(" ", strip=True)

            # Badges
            for badge in card.select("span.a-badge-text, span[data-component-type='s-status-badge-component']"):
                bt = badge.get_text(strip=True).lower()
                if "best seller" in bt:
                    best_seller = True
                if "amazon's choice" in bt or "amazons choice" in bt:
                    amazons_choice = True

            # Price — grab whole + fraction
            price_whole = card.select_one("span.a-price-whole")
            price_frac  = card.select_one("span.a-price-fraction")
            if price_whole:
                try:
                    p = float(price_whole.get_text(strip=True).replace(",", "").rstrip("."))
                    if price_frac:
                        p += float("0." + price_frac.get_text(strip=True))
                    if 0.5 < p < 5000:   # sanity-check
                        prices.append(p)
                except ValueError:
                    pass

            # Rating
            rating_el = card.select_one("span.a-icon-alt")
            if rating_el:
                m = re.search(r"([\d.]+) out of", rating_el.get_text())
                if m:
                    try:
                        ratings.append(float(m.group(1)))
                    except ValueError:
                        pass

            # Review count
            for span in card.select("span.a-size-base"):
                txt = span.get_text(strip=True).replace(",", "")
                if txt.isdigit() and int(txt) > 10:
                    review_counts.append(int(txt))
                    break

        seller_count = len(cards)

        result = {
            "amazon_result_count": result_count,
            "amazon_best_seller":  best_seller,
            "amazons_choice":      amazons_choice,
            "amazon_top_reviews":  max(review_counts) if review_counts else None,
            "amazon_avg_price":    round(sum(prices) / len(prices), 2) if prices else None,
            "amazon_avg_rating":   round(sum(ratings) / len(ratings), 2) if ratings else None,
            "amazon_seller_count": seller_count,
        }
        log.info("  Amazon '%s': %s results, BSB=%s, reviews=%s, price=$%s",
                 keyword, result_count, best_seller,
                 result["amazon_top_reviews"], result["amazon_avg_price"])
        return result

    except Exception as exc:
        log.warning("  Amazon fetch failed for '%s': %s", keyword, exc)
        return empty


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = datetime.utcnow().date().isoformat()

    # ── 1. Load keyword list ───────────────────────────────────────────────────
    keyword_records = load_keywords()
    kw_meta         = {k["keyword"]: k for k in keyword_records}

    # Keywords to actively fetch: everything except already-flat ones.
    # Flat keywords are preserved forever but we skip re-fetching — they keep
    # their last known trend data so the card stays visible on the dashboard.
    fetchable_records  = [k for k in keyword_records if k.get("status") != "flat"]
    fetchable_keywords = [k["keyword"] for k in fetchable_records]

    log.info("Tracking %d keywords total (%d active/new, %d flat — skipping re-fetch)",
             len(keyword_records), len(fetchable_keywords),
             len(keyword_records) - len(fetchable_keywords))

    # ── 2. Fetch Google Trends (active keywords only) ──────────────────────────
    raw = fetch_all_trends(fetchable_keywords)

    # ── 3. Detect flat trends & update keyword statuses ────────────────────────
    flat_count = 0
    for rec in fetchable_records:
        series = raw.get(rec["keyword"], [])
        if series and is_flat(series):
            if rec.get("status") != "flat":
                rec["status"]      = "flat"
                rec["flat_since"]  = today
                flat_count        += 1
                log.info("📉 Flat: %s", rec["keyword"])
        elif rec.get("status") == "flat" and series:
            # Recovered — reactivate
            peak       = max(series)
            recent_avg = sum(series[-3:]) / 3
            if recent_avg >= peak * 0.6:
                rec["status"] = "active"
                rec.pop("flat_since", None)
                log.info("📈 Recovered: %s", rec["keyword"])

    log.info("%d keywords newly marked as flat", flat_count)

    # ── 4. Discover new keywords ───────────────────────────────────────────────
    existing_set = {k["keyword"].lower() for k in keyword_records}
    new_keywords = discover_new_keywords(existing_set)
    keyword_records.extend(new_keywords)
    kw_meta.update({k["keyword"]: k for k in new_keywords})

    # ── 5. Fetch Reddit mentions (active keywords only) ────────────────────────
    log.info("Fetching Reddit mentions…")
    reddit_data = {}
    for i, kw in enumerate(fetchable_keywords):
        total, this_week, velocity = fetch_reddit_mentions(kw)
        reddit_data[kw] = {"reddit_30d": total, "reddit_7d": this_week, "reddit_velocity": velocity}
        if i < len(fetchable_keywords) - 1:
            time.sleep(1.5)

    # ── 6. Fetch Amazon data (active keywords only) ────────────────────────────
    log.info("Fetching Amazon data…")
    amazon_data = {}
    for i, kw in enumerate(fetchable_keywords):
        amazon_data[kw] = fetch_amazon_data(kw)
        if i < len(fetchable_keywords) - 1:
            time.sleep(random.uniform(2.5, 4.5))

    # ── 7. Build trends output ─────────────────────────────────────────────────
    # Load the previous cache so flat keywords can carry forward their last data
    prev_cache = {}
    if TRENDS_FILE.exists():
        try:
            prev = json.loads(TRENDS_FILE.read_text())
            prev_cache = {k["keyword"]: k for k in prev.get("keywords", [])}
        except Exception:
            pass

    keywords_out = []
    for idx, rec in enumerate(keyword_records, 1):
        kw     = rec["keyword"]
        status = rec.get("status", "active")

        if status == "flat" and kw in prev_cache:
            # Carry forward last known data — only update status/flat_since
            entry = dict(prev_cache[kw])
            entry["id"]         = idx
            entry["status"]     = "flat"
            entry["flat_since"] = rec.get("flat_since", entry.get("flat_since"))
            keywords_out.append(entry)
            continue

        series = raw.get(kw, [50] * 12)
        growth = compute_growth(series)
        rd     = reddit_data.get(kw, {})
        amz    = amazon_data.get(kw, {})

        viability, viability_breakdown = compute_viability(
            growth               = growth,
            series               = series,
            status               = status,
            reddit_30d           = rd.get("reddit_30d", 0),
            reddit_velocity      = rd.get("reddit_velocity"),
            amazon_result_count  = amz.get("amazon_result_count"),
            amazon_avg_price     = amz.get("amazon_avg_price"),
            amazon_avg_rating    = amz.get("amazon_avg_rating"),
            amazon_top_reviews   = amz.get("amazon_top_reviews"),
            amazon_best_seller   = amz.get("amazon_best_seller", False),
            amazons_choice       = amz.get("amazons_choice", False),
        )

        keywords_out.append({
            "id":               idx,
            "keyword":          kw,
            "category":         rec.get("category", "General"),
            "status":           status,
            "momentum":         classify_momentum(growth),
            "growth":           growth,
            "score":            trend_score(series, growth),
            "viability":        viability,
            "viability_breakdown": viability_breakdown,
            "trend":            series,
            "fetched":          datetime.utcnow().isoformat(),
            "is_new":           rec.get("is_new", False),
            "added":            rec.get("added"),
            "flat_since":       rec.get("flat_since"),
            # Reddit
            "reddit_30d":       rd.get("reddit_30d", 0),
            "reddit_7d":        rd.get("reddit_7d", 0),
            "reddit_velocity":  rd.get("reddit_velocity"),
            # Amazon
            "amazon_result_count":  amz.get("amazon_result_count"),
            "amazon_best_seller":   amz.get("amazon_best_seller", False),
            "amazons_choice":       amz.get("amazons_choice", False),
            "amazon_top_reviews":   amz.get("amazon_top_reviews"),
            "amazon_avg_price":     amz.get("amazon_avg_price"),
            "amazon_avg_rating":    amz.get("amazon_avg_rating"),
            "amazon_seller_count":  amz.get("amazon_seller_count"),
        })

    # Sort: active/new first (by growth desc), flat last
    keywords_out.sort(key=lambda k: (k["status"] == "flat", -k.get("growth", 0)))

    # ── 8. Save outputs ────────────────────────────────────────────────────────
    save_keywords(keyword_records)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.utcnow().isoformat(), "keywords": keywords_out}
    TRENDS_FILE.write_text(json.dumps(payload, indent=2))
    amz_ok    = sum(1 for k in keywords_out if k.get("amazon_result_count") is not None)
    flat_total = sum(1 for k in keywords_out if k.get("status") == "flat")
    log.info("✅ Saved %d keywords to %s (%d flat, %d new discovered, %d with Amazon data)",
             len(keywords_out), TRENDS_FILE, flat_total, len(new_keywords), amz_ok)


if __name__ == "__main__":
    main()
