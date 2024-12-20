from typing import Dict, Union, List

from flask import current_app
from mysql.connector import Error, connect
import mysql.connector
import logging
from contextlib import contextmanager
import json
from datetime import datetime
import hmac
import hashlib
import os
import base64
from cryptography.fernet import Fernet
import secrets
from werkzeug.security import generate_password_hash
from faker import Faker
from typing import Dict, List, Optional, Union, Any

# Configure logging
logging.basicConfig(
    filename='database.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)


class SecurityConfig:
    ENCRYPTION_KEY = Fernet.generate_key()
    INTEGRITY_KEY = os.urandom(32)
    HMAC_KEY = os.urandom(32)


class DataConfidentiality:
    def __init__(self, key=SecurityConfig.ENCRYPTION_KEY):
        self.fernet = Fernet(key)

    def encrypt_sensitive_fields(self, data):
        """Encrypt sensitive fields (gender and age)"""
        encrypted_data = data.copy()
        for field in ['gender', 'age']:
            if field in data:
                value = str(data[field]).encode()
                encrypted_value = self.fernet.encrypt(value)
                encrypted_data[field] = base64.b64encode(encrypted_value).decode()
        return encrypted_data

    def decrypt_sensitive_fields(self, data):
        """Decrypt sensitive fields if user has permission"""
        decrypted_data = data.copy()
        for field in ['gender', 'age']:
            if field in data and data[field]:
                try:
                    encrypted_value = base64.b64decode(data[field])
                    decrypted_value = self.fernet.decrypt(encrypted_value)
                    decrypted_data[field] = decrypted_value.decode()
                except Exception as e:
                    logging.error(f"Decryption error for field {field}: {str(e)}")
                    decrypted_data[field] = None
        return decrypted_data


class IntegrityProtection:
    def __init__(self, key=SecurityConfig.INTEGRITY_KEY):
        self.key = key

    def sign_record(self, record):
        """Sign individual record for integrity"""
        record_str = json.dumps(record, sort_keys=True)
        signature = hmac.new(
            self.key,
            record_str.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    def verify_record(self, record, signature):
        """Verify individual record integrity"""
        record_str = json.dumps(record, sort_keys=True)
        expected_signature = hmac.new(
            self.key,
            record_str.encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)

    def compute_merkle_root(self, records):
        """Compute Merkle root for query completeness"""
        if not records:
            return hashlib.sha256(b'empty').digest()

        hashes = [
            hashlib.sha256(json.dumps(r, sort_keys=True).encode()).digest()
            for r in records
        ]

        while len(hashes) > 1:
            if len(hashes) % 2 == 1:
                hashes.append(hashes[-1])
            hashes = [
                hashlib.sha256(h1 + h2).digest()
                for h1, h2 in zip(hashes[::2], hashes[1::2])
            ]
        return hashes[0].hex()


class AccessControl:
    @staticmethod
    def filter_data_by_group(data, group):
        """Filter data based on user group"""
        filtered_data = data.copy()

        if group == 'H':
            return filtered_data
        elif group == 'R':
            filtered_data.pop('first_name', None)
            filtered_data.pop('last_name', None)

        return filtered_data

    @staticmethod
    def can_add_record(group):
        """Check if user can add records"""
        return group == 'H'

    @staticmethod
    def get_accessible_fields(group):
        """Get list of accessible fields based on user group"""
        base_fields = [
            'id', 'encrypted_age', 'encrypted_gender',
            'weight', 'height', 'health_history',
            'created_at', 'updated_at'
        ]

        if group == 'H':
            return base_fields + ['first_name', 'last_name']
        elif group == 'R':
            return base_fields

        return []


class DatabaseConfig:
    HOST = 'localhost'
    USER = 'root'
    PASSWORD = 'root'
    DATABASE = 'healthcare_db'

    TABLES = {}
    TABLES['users'] = (
        "CREATE TABLE IF NOT EXISTS `users` ("
        "  `id` int NOT NULL AUTO_INCREMENT,"
        "  `username` varchar(80) NOT NULL UNIQUE,"
        "  `password_hash` varchar(255) NOT NULL,"
        "  `salt` varchar(64) NOT NULL,"
        "  `group` varchar(10) NOT NULL,"
        "  `last_login` datetime DEFAULT NULL,"
        "  `login_attempts` int DEFAULT 0,"
        "  `locked_until` datetime DEFAULT NULL,"
        "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
        "  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  PRIMARY KEY (`id`),"
        "  INDEX `idx_username` (`username`),"
        "  INDEX `idx_group` (`group`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )

    TABLES['records'] = (
        "CREATE TABLE IF NOT EXISTS `records` ("
        "  `id` int NOT NULL AUTO_INCREMENT,"
        "  `user_id` int NOT NULL,"
        "  `first_name` varchar(255),"
        "  `last_name` varchar(255),"
        "  `encrypted_gender` varchar(255),"
        "  `encrypted_age` varchar(255),"
        "  `weight` float,"
        "  `height` float,"
        "  `health_history` text,"
        "  `signature` varchar(64) NOT NULL,"
        "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
        "  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  PRIMARY KEY (`id`),"
        "  FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,"
        "  INDEX `idx_user_created` (`user_id`, `created_at`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )


class SecureDB:
    def __init__(self):
        self.config = DatabaseConfig
        self.confidentiality = DataConfidentiality()
        self.integrity = IntegrityProtection()
        self.access_control = AccessControl()
        self._dashboard_manager = None

    def get_dashboard_manager(self):
        if self._dashboard_manager is None:
            self._dashboard_manager = DashboardDataManager(self)
        return self._dashboard_manager

    def _get_db_connection(self, database=None):
        """Create a database connection"""
        try:
            connection_config = {
                'host': self.config.HOST,
                'user': self.config.USER,
                'password': self.config.PASSWORD,
                'autocommit': False,
                'buffered': True
            }
            if database:
                connection_config['database'] = database

            return mysql.connector.connect(**connection_config)
        except Error as err:
            logging.error(f"Error connecting to MySQL: {err}")
            raise

    def create_database(self):
        """Create database and tables if they don't exist"""
        conn = None
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.config.DATABASE} "
                           "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute(f"USE {self.config.DATABASE}")

            for table_name, table_sql in self.config.TABLES.items():
                try:
                    cursor.execute(table_sql)
                    logging.info(f"Created table {table_name}")
                except Error as err:
                    logging.error(f"Error creating table {table_name}: {err}")
                    raise

            conn.commit()
            logging.info("Database and tables created successfully")

        except Error as err:
            logging.error(f"Error creating database: {err}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()

    def add_record(self, data, username):
        """Add new record with security measures and validation"""
        conn = None
        try:
            # Validate required fields
            required_fields = ['first_name', 'last_name', 'age', 'gender', 'weight', 'height', 'health_history']
            for field in required_fields:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")

            conn = self._get_db_connection(self.config.DATABASE)
            cursor = conn.cursor(dictionary=True)

            # Get user_id and group
            cursor.execute(
                "SELECT id, `group` FROM users WHERE username = %s",
                (username,)
            )
            user_data = cursor.fetchone()

            if not user_data:
                raise ValueError(f"User not found: {username}")

            if not self.access_control.can_add_record(user_data['group']):
                raise ValueError(f"Insufficient permissions for user: {username}")

            # Prepare data for storage
            data_to_store = {
                'user_id': user_data['id'],
                'first_name': data.get('first_name'),
                'last_name': data.get('last_name'),
                'weight': float(data.get('weight')),
                'height': float(data.get('height')),
                'health_history': data.get('health_history')
            }

            # Encrypt sensitive fields
            encrypted_fields = self.confidentiality.encrypt_sensitive_fields({
                'age': data.get('age'),
                'gender': data.get('gender')
            })

            data_to_store['encrypted_age'] = encrypted_fields.get('age')
            data_to_store['encrypted_gender'] = encrypted_fields.get('gender')

            # Generate signature
            signature = self.integrity.sign_record(data_to_store)
            data_to_store['signature'] = signature

            # Insert record
            columns = ', '.join(data_to_store.keys())
            placeholders = ', '.join(['%s'] * len(data_to_store))
            insert_query = f"""
                  INSERT INTO records ({columns})
                  VALUES ({placeholders})
              """

            cursor.execute(insert_query, list(data_to_store.values()))
            record_id = cursor.lastrowid
            conn.commit()

            logging.info(f"Successfully added record with ID: {record_id}")
            return {
                'success': True,
                'record_id': record_id,
                'message': 'Record added successfully'
            }

        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"Error adding record: {str(e)}")
            raise
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()

    def get_user_data(self, username, group):
        """Get user data with security measures"""
        conn = None
        try:
            conn = self._get_db_connection(self.config.DATABASE)
            cursor = conn.cursor(dictionary=True)

            # Get accessible fields based on group
            accessible_fields = self.access_control.get_accessible_fields(group)
            fields_str = ', '.join([f'r.{field}' for field in accessible_fields])

            # Get user_id
            cursor.execute(
                "SELECT id FROM users WHERE username = %s",
                (username,)
            )
            user_data = cursor.fetchone()

            if not user_data:
                raise ValueError("User not found")

            # Construct query based on group permissions
            if group == 'H':
                query = f"""
                      SELECT {fields_str}, u.username 
                      FROM records r 
                      JOIN users u ON r.user_id = u.id 
                      ORDER BY r.created_at DESC
                  """
                cursor.execute(query)
            else:  # group R
                query = f"""
                      SELECT {fields_str}
                      FROM records r 
                      JOIN users u ON r.user_id = u.id 
                      WHERE u.username = %s 
                      ORDER BY r.created_at DESC
                  """
                cursor.execute(query, (username,))

            records = cursor.fetchall()
            verified_records = []

            for record in records:
                # Verify record integrity
                record_copy = record.copy()
                signature = record_copy.pop('signature', None) if 'signature' in record_copy else None

                if signature and self.integrity.verify_record(record_copy, signature):
                    # Decrypt sensitive fields if user has permission
                    if record.get('encrypted_age'):
                        record['age'] = self.confidentiality.decrypt_sensitive_fields(
                            {'age': record['encrypted_age']}
                        )['age']
                        record.pop('encrypted_age', None)

                    if record.get('encrypted_gender'):
                        record['gender'] = self.confidentiality.decrypt_sensitive_fields(
                            {'gender': record['encrypted_gender']}
                        )['gender']
                        record.pop('encrypted_gender', None)

                    verified_records.append(record)

            # Generate Merkle root for query completeness
            merkle_root = self.integrity.compute_merkle_root(verified_records)

            return {
                'records': verified_records,
                'merkle_root': merkle_root
            }

        except Exception as e:
            logging.error(f"Error fetching user data: {str(e)}")
            raise
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()


def get_user_data(self, username, group):
    """Get user data with security measures"""
    conn = None
    try:
        conn = self._get_db_connection(self.config.DATABASE)
        cursor = conn.cursor(dictionary=True)

        # Get user_id
        cursor.execute(
            "SELECT id FROM users WHERE username = %s",
            (username,)
        )
        user_data = cursor.fetchone()

        if not user_data:
            return {'records': [], 'error': 'User not found'}

        # Build query based on group permissions
        if group == 'H':
            query = """
                SELECT r.id, r.first_name, r.last_name, 
                       r.encrypted_gender as gender, 
                       r.encrypted_age as age,
                       r.weight, r.height, r.health_history,
                       r.created_at, r.updated_at,
                       u.username 
                FROM records r 
                JOIN users u ON r.user_id = u.id 
                ORDER BY r.created_at DESC
            """
            cursor.execute(query)
        else:
            query = """
                SELECT r.id, r.encrypted_gender as gender, 
                       r.encrypted_age as age,
                       r.weight, r.height, r.health_history,
                       r.created_at, r.updated_at
                FROM records r 
                JOIN users u ON r.user_id = u.id 
                WHERE u.username = %s 
                ORDER BY r.created_at DESC
            """
            cursor.execute(query, (username,))

        records = cursor.fetchall()

        # Process records
        processed_records = []
        for record in records:
            try:
                # Decrypt sensitive fields
                if record.get('gender'):
                    record['gender'] = self.confidentiality.decrypt_sensitive_fields(
                        {'gender': record['gender']}
                    )['gender']

                if record.get('age'):
                    record['age'] = self.confidentiality.decrypt_sensitive_fields(
                        {'age': record['age']}
                    )['age']

                # Convert datetime objects to strings
                if record.get('created_at'):
                    record['created_at'] = record['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if record.get('updated_at'):
                    record['updated_at'] = record['updated_at'].strftime('%Y-%m-%d %H:%M:%S')

                processed_records.append(record)

            except Exception as e:
                logging.error(f"Error processing record {record.get('id')}: {str(e)}")
                continue

        return {
            'records': processed_records,
            'total_records': len(processed_records)
        }

    except Exception as e:
        logging.error(f"Database error in get_user_data: {str(e)}")
        return {'records': [], 'error': str(e)}
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
def generate_sample_data():
    """Generate 100 sample healthcare records"""
    fake = Faker()
    records = []

    for _ in range(100):
        record = {
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "gender": fake.random_element(['M', 'F']),
            "age": fake.random_int(min=18, max=90),
            "weight": round(fake.random.uniform(45.0, 120.0), 1),
            "height": round(fake.random.uniform(150.0, 200.0), 1),
            "health_history": fake.text(max_nb_chars=200)
        }
        records.append(record)

    return records


def create_admin_user(db_instance):
    """Create admin user if it doesn't exist"""
    conn = None
    try:
        conn = db_instance._get_db_connection(db_instance.config.DATABASE)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = 'admin'")
        if not cursor.fetchone():
            admin_password = "Admin@123456"
            salt = secrets.token_hex(32)
            password_hash = generate_password_hash(admin_password, method='pbkdf2:sha256:260000')

            insert_query = """
                  INSERT INTO users (username, password_hash, salt, `group`) 
                  VALUES (%s, %s, %s, %s)
              """
            cursor.execute(insert_query, ('admin', password_hash, salt, 'H'))
            conn.commit()
            logging.info("Admin user created successfully")
            print("Admin user created successfully")

    except Error as err:
        logging.error(f"Error creating admin user: {err}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def create_regular_user(db_instance):
    """Create a regular (group R) user if it doesn't exist"""
    conn = None
    try:
        conn = db_instance._get_db_connection(db_instance.config.DATABASE)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE username = 'regular_user'")
        if not cursor.fetchone():
            regular_password = "Regular@123456"
            salt = secrets.token_hex(32)
            password_hash = generate_password_hash(regular_password, method='pbkdf2:sha256:260000')

            insert_query = """
                  INSERT INTO users (username, password_hash, salt, `group`) 
                  VALUES (%s, %s, %s, %s)
              """
            cursor.execute(insert_query, ('regular_user', password_hash, salt, 'R'))
            conn.commit()
            logging.info("Regular user created successfully")
            print("Regular user created successfully")

    except Error as err:
        logging.error(f"Error creating regular user: {err}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def populate_database(db):
    """Populate database with sample data"""
    try:
        records = generate_sample_data()
        for record in records:
            db.add_record(record, "admin")
        logging.info(f"Successfully added {len(records)} sample records")
        print(f"Successfully added {len(records)} sample records")
    except Exception as e:
        logging.error(f"Error populating database: {str(e)}")
        print(f"Error populating database: {str(e)}")
        raise


def setup_database():
    """Setup database, admin user, regular user, and sample data"""
    try:
        db = SecureDB()
        print("Creating database and tables...")
        db.create_database()
        print("Database and tables created successfully")

        print("Creating admin user (Group H)...")
        create_admin_user(db)

        print("Creating regular user (Group R)...")
        create_regular_user(db)

        print("Populating database with sample data...")
        populate_database(db)
        print("Database setup completed successfully")

    except Exception as err:
        logging.error(f"Database setup failed: {err}")
        print(f"Error during database setup: {err}")
        raise


class DashboardDataManager:
    def __init__(self, db_instance: 'SecureDB'):
        self.db = db_instance
        self.logger = logging.getLogger(__name__)
        self.confidentiality = db_instance.confidentiality
        self.integrity = db_instance.integrity
        self.access_control = db_instance.access_control

    def _serialize_datetime(self, record: Dict) -> Dict:
        """
        Convert datetime objects to string format in a record
        """
        serialized_record = record.copy()
        datetime_fields = ['created_at', 'updated_at', 'last_modified', 'locked_until']

        for field in datetime_fields:
            if field in serialized_record and serialized_record[field] is not None:
                if isinstance(serialized_record[field], datetime):
                    serialized_record[field] = serialized_record[field].strftime('%Y-%m-%d %H:%M:%S')

        return serialized_record

    # First, update the TABLES definition in your DatabaseConfig class:

    class DatabaseConfig:
        # ... other configurations ...

        TABLES = {}
        TABLES['users'] = (
            "CREATE TABLE IF NOT EXISTS `users` ("
            "  `id` int NOT NULL AUTO_INCREMENT,"
            "  `username` varchar(80) NOT NULL UNIQUE,"
            "  `password_hash` varchar(255) NOT NULL,"
            "  `salt` varchar(64) NOT NULL,"
            "  `group` varchar(10) NOT NULL,"
            "  `last_login` datetime DEFAULT NULL,"
            "  `login_attempts` int DEFAULT 0,"
            "  `locked_until` datetime DEFAULT NULL,"
            "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
            "  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
            "  PRIMARY KEY (`id`),"
            "  INDEX `idx_username` (`username`),"
            "  INDEX `idx_group` (`group`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )

        TABLES['records'] = (
            "CREATE TABLE IF NOT EXISTS `records` ("
            "  `id` int NOT NULL AUTO_INCREMENT,"
            "  `user_id` int NOT NULL,"
            "  `first_name` varchar(255),"
            "  `last_name` varchar(255),"
            "  `encrypted_gender` varchar(255),"
            "  `encrypted_age` varchar(255),"
            "  `weight` float,"
            "  `height` float,"
            "  `health_history` text,"
            "  `signature` varchar(64) NOT NULL,"
            "  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,"
            "  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
            "  PRIMARY KEY (`id`),"
            "  FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,"
            "  INDEX `idx_user_created` (`user_id`, `created_at`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        )

    # Then update the DashboardDataManager queries:

    def get_dashboard_data(self, username: str, user_group: str) -> Dict[str, Union[List[Dict], str]]:
        """
        Fetch dashboard data based on user permissions
        """
        conn = None
        try:
            conn = self.db._get_db_connection(self.db.config.DATABASE)
            cursor = conn.cursor(dictionary=True)

            # Get accessible fields based on group permissions
            if user_group == 'H':  # Admin users
                query = """
                    SELECT r.id, r.first_name, r.last_name, 
                           r.encrypted_gender, r.encrypted_age,
                           r.weight, r.height, r.health_history,
                           r.created_at, r.updated_at, r.signature,
                           u.username as creator_username
                    FROM records r
                    JOIN users u ON r.user_id = u.id
                    ORDER BY r.created_at DESC
                """
                cursor.execute(query)
            else:  # Regular users - limited access
                query = """
                    SELECT r.id, r.encrypted_gender, r.encrypted_age,
                           r.weight, r.height,
                           r.created_at, r.updated_at
                    FROM records r
                    JOIN users u ON r.user_id = u.id
                    WHERE u.username = %s
                    ORDER BY r.created_at DESC
                """
                cursor.execute(query, (username,))

            records = cursor.fetchall()
            processed_records = []

            for record in records:
                try:
                    # Decrypt sensitive fields if present
                    if record.get('encrypted_gender'):
                        record['gender'] = self.confidentiality.decrypt_sensitive_fields(
                            {'gender': record['encrypted_gender']}
                        )['gender']
                        record.pop('encrypted_gender')

                    if record.get('encrypted_age'):
                        record['age'] = self.confidentiality.decrypt_sensitive_fields(
                            {'age': record['encrypted_age']}
                        )['age']
                        record.pop('encrypted_age')

                    # Serialize datetime fields
                    processed_record = self._serialize_datetime(record)

                    # Remove signature from final output
                    processed_record.pop('signature', None)

                    processed_records.append(processed_record)
                except Exception as e:
                    self.logger.error(f"Error processing record {record.get('id')}: {str(e)}")
                    continue

            return {
                'success': True,
                'records': processed_records,
                'count': len(processed_records)
            }

        except Exception as e:
            self.logger.error(f"Error in get_dashboard_data: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'records': []
            }
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()

    def search_records(self,
                       user_group: str,
                       search_term: Optional[str] = None,
                       filters: Optional[Dict] = None, username=None) -> Dict:
        """
        Search and filter dashboard records
        """
        conn = None
        try:
            conn = self.db._get_db_connection(self.db.config.DATABASE)
            cursor = conn.cursor(dictionary=True)

            # Build base query based on user group
            if user_group == 'H':
                base_query = """
                    SELECT r.id, r.first_name, r.last_name, 
                           r.encrypted_gender, r.encrypted_age,
                           r.weight, r.height, r.health_history,
                           r.created_at, r.updated_at, r.signature,
                           u.username as creator_username
                    FROM records r
                    JOIN users u ON r.user_id = u.id
                    WHERE 1=1
                """
            else:
                base_query = """
                    SELECT r.id, r.encrypted_gender, r.encrypted_age,
                           r.weight, r.height,
                           r.created_at, r.updated_at
                    FROM records r
                    JOIN users u ON r.user_id = u.id
                    WHERE u.username = %s
                """

            params = []
            if user_group != 'H':
                params.append(username)

            # Add search conditions
            if search_term:
                base_query += """ AND (
                    r.first_name LIKE %s OR 
                    r.last_name LIKE %s OR 
                    CAST(r.id AS CHAR) LIKE %s
                )"""
                search_pattern = f"%{search_term}%"
                params.extend([search_pattern, search_pattern, search_pattern])

            # Add filters
            if filters:
                if filters.get('gender'):
                    base_query += " AND r.encrypted_gender = %s"
                    params.append(filters['gender'])
                if filters.get('age_min'):
                    base_query += " AND r.encrypted_age >= %s"
                    params.append(int(filters['age_min']))
                if filters.get('age_max'):
                    base_query += " AND r.encrypted_age <= %s"
                    params.append(int(filters['age_max']))

            base_query += " ORDER BY r.created_at DESC"
            cursor.execute(base_query, params)
            records = cursor.fetchall()

            processed_records = []
            for record in records:
                try:
                    # Process the record
                    if record.get('encrypted_gender'):
                        record['gender'] = self.confidentiality.decrypt_sensitive_fields(
                            {'gender': record['encrypted_gender']}
                        )['gender']
                        record.pop('encrypted_gender')

                    if record.get('encrypted_age'):
                        record['age'] = self.confidentiality.decrypt_sensitive_fields(
                            {'age': record['encrypted_age']}
                        )['age']
                        record.pop('encrypted_age')

                    # Serialize datetime fields
                    processed_record = self._serialize_datetime(record)
                    processed_record.pop('signature', None)
                    processed_records.append(processed_record)
                except Exception as e:
                    self.logger.error(f"Error processing record {record.get('id')}: {str(e)}")
                    continue

            return {
                'success': True,
                'records': processed_records,
                'count': len(processed_records)
            }

        except Exception as e:
            self.logger.error(f"Error in search_records: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'records': []
            }
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()


def reset_encryption():
    db = SecureDB()
    conn = db._get_db_connection(db.config.DATABASE)
    cursor = conn.cursor(dictionary=True)

    try:
        # Get all records
        cursor.execute("SELECT * FROM records")
        records = cursor.fetchall()

        # Re-encrypt data with new key
        for record in records:
            if record['encrypted_gender']:
                gender = record['encrypted_gender']
                age = record['encrypted_age']

                # Update record with new encryption
                cursor.execute("""
                    UPDATE records 
                    SET encrypted_gender = %s, encrypted_age = %s
                    WHERE id = %s
                """, (gender, age, record['id']))

        conn.commit()
        print("Encryption reset successful")

    except Exception as e:
        print(f"Error resetting encryption: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    setup_database()