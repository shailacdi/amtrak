#!/usr/bin/env python3
"""
Amtrak Price Monitor - Enhanced with Selenium
More reliable web scraping using browser automation
"""

import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import sqlite3
from twilio.rest import Client
from dotenv import load_dotenv
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Load environment variables
load_dotenv()

# Configuration
PRICE_THRESHOLD = 20.00
CHECK_INTERVAL_MINUTES = 120

# Station information
STATIONS = {
    'PJC': {'name': 'Princeton Junction', 'state': 'NJ'},
    'PHL': {'name': 'Philadelphia, PA - 30th Street Station', 'state': 'PA'},
    'TRE': {'name': 'Trenton', 'state': 'NJ'}
}

# Time windows
MORNING_START = "08:00"
MORNING_END = "08:30"
AFTERNOON_START = "14:00"
AFTERNOON_END = "17:00"

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
YOUR_PHONE_NUMBER = os.getenv('YOUR_PHONE_NUMBER')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('amtrak_monitor_selenium.log'),
        logging.StreamHandler()
    ]
)


class AmtrakSeleniumMonitor:
    """Monitor Amtrak prices using Selenium for reliable scraping"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self.db_conn = self.init_database()
        self.twilio_client = None
        
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            self.twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            logging.info("Twilio client initialized")
    
    def init_database(self) -> sqlite3.Connection:
        """Initialize SQLite database"""
        conn = sqlite3.connect('amtrak_prices.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                travel_date DATE,
                origin TEXT,
                destination TEXT,
                departure_time TEXT,
                arrival_time TEXT,
                train_number TEXT,
                duration TEXT,
                price REAL,
                route_type TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                travel_date DATE,
                route_type TEXT,
                train_number TEXT,
                price REAL,
                message TEXT
            )
        ''')
        conn.commit()
        return conn
    
    def init_driver(self):
        """Initialize Selenium WebDriver"""
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument('--headless')
        
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        self.driver = webdriver.Chrome(options=chrome_options)
        logging.info("Selenium WebDriver initialized")
    
    def close_driver(self):
        """Close Selenium WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def get_working_days(self, num_days: int = 5) -> List[datetime]:
        """Get next N working days"""
        working_days = []
        current_date = datetime.now()
        
        while len(working_days) < num_days:
            current_date += timedelta(days=1)
            if current_date.weekday() < 5:  # Monday = 0, Friday = 4
                working_days.append(current_date)
        
        return working_days
    
    def search_trains(self, origin: str, destination: str, travel_date: datetime) -> List[Dict]:
        """
        Search for trains on Amtrak website using Selenium
        """
        if not self.driver:
            self.init_driver()
        
        date_str = travel_date.strftime("%m/%d/%Y")
        
        try:
            # Navigate to Amtrak homepage
            logging.info(f"Searching trains from {origin} to {destination} on {date_str}")
            self.driver.get("https://www.amtrak.com/")
            
            # Wait for page to load
            wait = WebDriverWait(self.driver, 15)
            
            # Fill in origin
            origin_input = wait.until(
                EC.presence_of_element_located((By.ID, "from"))
            )
            origin_input.clear()
            origin_input.send_keys(STATIONS[origin]['name'])
            time.sleep(1)
            
            # Select from dropdown
            origin_option = wait.until(
                EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), '{STATIONS[origin]['name']}')]"))
            )
            origin_option.click()
            
            # Fill in destination
            dest_input = wait.until(
                EC.presence_of_element_located((By.ID, "to"))
            )
            dest_input.clear()
            dest_input.send_keys(STATIONS[destination]['name'])
            time.sleep(1)
            
            # Select from dropdown
            dest_option = wait.until(
                EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), '{STATIONS[destination]['name']}')]"))
            )
            dest_option.click()
            
            # Fill in date
            date_input = wait.until(
                EC.presence_of_element_located((By.ID, "departDate"))
            )
            date_input.clear()
            date_input.send_keys(date_str)
            
            # Click search button
            search_button = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Find Trains')]"))
            )
            search_button.click()
            
            # Wait for results page to load
            time.sleep(5)
            
            # Extract train information
            trains = self.extract_train_data()
            
            logging.info(f"Found {len(trains)} trains")
            return trains
        
        except TimeoutException as e:
            logging.error(f"Timeout waiting for page elements: {e}")
            return []
        except Exception as e:
            logging.error(f"Error searching trains: {e}", exc_info=True)
            return []
    
    def extract_train_data(self) -> List[Dict]:
        """Extract train data from search results page"""
        trains = []
        
        try:
            wait = WebDriverWait(self.driver, 10)
            
            # Wait for train results
            train_elements = wait.until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "train-result"))
            )
            print(f"### TRAINS #### {}", train_elements)
            for train_elem in train_elements:
                try:
                    # Extract train details
                    # Note: CSS selectors may need adjustment based on actual Amtrak website
                    
                    train_number = train_elem.find_element(By.CLASS_NAME, "train-number").text
                    departure_time = train_elem.find_element(By.CLASS_NAME, "departure-time").text
                    arrival_time = train_elem.find_element(By.CLASS_NAME, "arrival-time").text
                    duration = train_elem.find_element(By.CLASS_NAME, "duration").text
                    
                    # Extract price
                    price_elem = train_elem.find_element(By.CLASS_NAME, "price")
                    price_text = price_elem.text.replace('$', '').replace(',', '').strip()
                    price = float(price_text)
                    
                    trains.append({
                        'train_number': train_number,
                        'departure_time': departure_time,
                        'arrival_time': arrival_time,
                        'duration': duration,
                        'price': price,
                        'travel_date': self.driver.find_element(By.ID, "travel-date").get_attribute('value')
                    })
                
                except (NoSuchElementException, ValueError) as e:
                    logging.warning(f"Failed to parse train element: {e}")
                    continue
        
        except TimeoutException:
            logging.warning("No train results found")
        except Exception as e:
            logging.error(f"Error extracting train data: {e}")
        
        return trains
    
    def filter_by_time_window(self, trains: List[Dict], start_time: str, end_time: str) -> List[Dict]:
        """Filter trains by departure time window"""
        filtered = []
        
        for train in trains:
            try:
                dep_time = train['departure_time']
                dep_time_obj = self.parse_time(dep_time)
                start_time_obj = self.parse_time(start_time)
                end_time_obj = self.parse_time(end_time)
                
                if start_time_obj <= dep_time_obj <= end_time_obj:
                    filtered.append(train)
            except Exception as e:
                logging.warning(f"Failed to parse time: {e}")
                continue
        
        return filtered
    
    def parse_time(self, time_str: str) -> datetime:
        """Parse time string"""
        time_str = time_str.strip().upper()
        
        formats = ["%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S"]
        
        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        
        raise ValueError(f"Unable to parse time: {time_str}")
    
    def save_price_data(self, train: Dict, origin: str, destination: str, route_type: str):
        """Save price data to database"""
        cursor = self.db_conn.cursor()
        cursor.execute('''
            INSERT INTO price_history 
            (travel_date, origin, destination, departure_time, arrival_time, 
             train_number, duration, price, route_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            train['travel_date'],
            origin,
            destination,
            train['departure_time'],
            train['arrival_time'],
            train['train_number'],
            train.get('duration', ''),
            train['price'],
            route_type
        ))
        self.db_conn.commit()
    
    def check_if_notified(self, train: Dict, route_type: str) -> bool:
        """Check if already notified for this train"""
        cursor = self.db_conn.cursor()
        cursor.execute('''
            SELECT id FROM notifications
            WHERE travel_date = ? AND route_type = ? AND train_number = ?
            AND sent_timestamp > datetime('now', '-24 hours')
        ''', (train['travel_date'], route_type, train['train_number']))
        
        return cursor.fetchone() is not None
    
    def send_sms_alert(self, train: Dict, origin: str, destination: str, route_type: str):
        """Send SMS alert"""
        if not self.twilio_client:
            logging.warning("Twilio not configured")
            return False
        
        message = (
            f"🚂 Amtrak Alert!\n\n"
            f"{route_type}\n"
            f"{origin} → {destination}\n"
            f"Date: {train['travel_date']}\n"
            f"Train {train['train_number']}\n"
            f"Depart: {train['departure_time']}\n"
            f"💰 ${train['price']:.2f}\n\n"
            f"Book: amtrak.com"
        )
        
        try:
            sms = self.twilio_client.messages.create(
                body=message,
                from_=TWILIO_PHONE_NUMBER,
                to=YOUR_PHONE_NUMBER
            )
            
            cursor = self.db_conn.cursor()
            cursor.execute('''
                INSERT INTO notifications 
                (travel_date, route_type, train_number, price, message)
                VALUES (?, ?, ?, ?, ?)
            ''', (train['travel_date'], route_type, train['train_number'], train['price'], message))
            self.db_conn.commit()
            
            logging.info(f"SMS sent: {sms.sid}")
            return True
        except Exception as e:
            logging.error(f"SMS failed: {e}")
            return False
    
    def check_route(self, origin: str, destination: str, travel_date: datetime, 
                    time_start: str, time_end: str, route_type: str):
        """Check a specific route"""
        trains = self.search_trains(origin, destination, travel_date)
        filtered_trains = self.filter_by_time_window(trains, time_start, time_end)
        
        for train in filtered_trains:
            self.save_price_data(train, origin, destination, route_type)
            
            if train['price'] < PRICE_THRESHOLD:
                if not self.check_if_notified(train, route_type):
                    logging.info(f"ALERT: Train {train['train_number']} at ${train['price']}")
                    self.send_sms_alert(
                        train,
                        STATIONS[origin]['name'],
                        STATIONS[destination]['name'],
                        route_type
                    )
        
        return filtered_trains
    
    def run_monitoring_cycle(self):
        """Run complete monitoring cycle"""
        logging.info("="*60)
        logging.info("Starting monitoring cycle")
        
        working_days = self.get_working_days(5)
        
        for travel_date in working_days:
            logging.info(f"\n📅 {travel_date.strftime('%A, %B %d, %Y')}")
            
            # Morning outbound: PJC → PHL
            logging.info("Checking morning outbound...")
            morning_trains = self.check_route(
                'PJC', 'PHL', travel_date,
                MORNING_START, MORNING_END,
                'MORNING_OUTBOUND'
            )
            logging.info(f"Found {len(morning_trains)} morning trains")
            
            time.sleep(3)  # Delay between searches
            
            # Afternoon return: PHL → PJC
            logging.info("Checking afternoon return to PJC...")
            afternoon_pjc = self.check_route(
                'PHL', 'PJC', travel_date,
                AFTERNOON_START, AFTERNOON_END,
                'AFTERNOON_RETURN_PJC'
            )
            
            time.sleep(3)
            
            # Afternoon return: PHL → TRE
            logging.info("Checking afternoon return to Trenton...")
            afternoon_tre = self.check_route(
                'PHL', 'TRE', travel_date,
                AFTERNOON_START, AFTERNOON_END,
                'AFTERNOON_RETURN_TRE'
            )
            
            total_afternoon = len(afternoon_pjc) + len(afternoon_tre)
            logging.info(f"Found {total_afternoon} afternoon return trains")
        
        logging.info("\nMonitoring cycle complete")
        logging.info("="*60)
    
    def run_continuously(self):
        """Run continuous monitoring"""
        logging.info("Starting continuous monitoring")
        logging.info(f"Price threshold: ${PRICE_THRESHOLD}")
        logging.info(f"Check interval: {CHECK_INTERVAL_MINUTES} minutes")
        
        while True:
            try:
                self.run_monitoring_cycle()
                
                next_check = datetime.now() + timedelta(minutes=CHECK_INTERVAL_MINUTES)
                logging.info(f"\nNext check: {next_check.strftime('%I:%M %p')}")
                
                # Close driver to free resources
                self.close_driver()
                
                time.sleep(CHECK_INTERVAL_MINUTES * 60)
            
            except KeyboardInterrupt:
                logging.info("Stopped by user")
                break
            except Exception as e:
                logging.error(f"Error: {e}", exc_info=True)
                self.close_driver()
                time.sleep(300)
    
    def __del__(self):
        """Cleanup"""
        self.close_driver()
        if hasattr(self, 'db_conn'):
            self.db_conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--monitor', action='store_true', help='Run continuously')
    parser.add_argument('--no-headless', action='store_true', help='Show browser')
    args = parser.parse_args()
    
    monitor = AmtrakSeleniumMonitor(headless=not args.no_headless)
    
    if args.monitor:
        monitor.run_continuously()
    else:
        monitor.run_monitoring_cycle()
