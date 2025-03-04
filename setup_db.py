from pymongo import MongoClient


client = MongoClient('mongodb://localhost:27017/')

# Create database
db = client['face_recognition']

# Create collections
users_collection = db['users']
requests_collection = db['registration_requests']
categories_collection = db['categories']
reports_collection = db['reports']

# Create indexes for efficient queries
users_collection.create_index([('username', 1)], unique=True)
requests_collection.create_index([('username', 1)], unique=True)
categories_collection.create_index([('name', 1)], unique=True)
reports_collection.create_index([('category', 1), ('matches.Celebrity', 1)])


users_collection.insert_one({
    'username': 'superadmin',
    'password': 'superadmin',  
    'category': None,
    'role':'superadmin'
})


categories_collection.insert_many([
    {'name': 'celebrities'},
    {'name': 'sportsmen'},
    {'name': 'criminals'}
])

print("Database setup completed.")
