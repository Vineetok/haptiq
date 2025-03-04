from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file
from pymongo import MongoClient
from gridfs import GridFS
from bson import ObjectId
from bson.errors import InvalidId
import io
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Connect to MongoDB and select database/collections
client = MongoClient('mongodb://localhost:27017/')
db = client['face_recognition']
fs = GridFS(db)

users_collection = db['users']
categories_collection = db['categories']
reports_collection = db['reports']
requests_collection = db['registration_requests']

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = users_collection.find_one({'username': username, 'password': password})
        if user:
            session['username'] = username
            session['role'] = user.get('role', '')
            if 'categories' in user:
                session['categories'] = user['categories']
            else:
                session['categories'] = [user.get('category', '')]
            if user.get('role') == 'superadmin':
                return redirect(url_for('approve_requests'))
            elif user.get('role') == 'categoryadmin':
                session['category'] = user.get('category')
                return redirect(url_for('category_dashboard'))
            else:
                return redirect(url_for('report'))
        else:
            return render_template('login.html', error='Incorrect username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        selected_categories = request.form.getlist('categories')
        role = 'user'
        requests_collection.insert_one({
            'username': username,
            'password': password,
            'role': role,
            'categories': selected_categories
        })
        flash('Registration request sent for approval.')
        return redirect(url_for('login'))
    categories = list(categories_collection.find())
    return render_template('register.html', categories=categories)

@app.route('/remove_category_admin', methods=['POST'])
def remove_category_admin():
    if 'username' not in session or session.get('role') != 'superadmin':
        flash("Unauthorized access")
        return redirect(url_for('approve_requests'))
    category = request.form.get('category')
    if not category:
        flash("Invalid request")
        return redirect(url_for('approve_requests'))
    result = users_collection.delete_one({'role': 'categoryadmin', 'category': category})
    if result.deleted_count > 0:
        flash(f"Category Admin for '{category}' removed successfully")
    else:
        flash(f"No Category Admin found for '{category}'")
    return redirect(url_for('approve_requests'))

@app.route('/remove_sub_user', methods=['POST'])
def remove_sub_user():
    if 'username' not in session or session.get('role') not in ['superadmin', 'categoryadmin']:
        flash("Unauthorized access")
        return redirect(url_for('login'))
    username = request.form.get('username')
    category = request.form.get('category')
    if not username or not category:
        flash("Invalid request")
        return redirect(url_for('category_dashboard'))
    if session.get('role') == 'categoryadmin' and session.get('category') != category:
        flash("Unauthorized removal attempt")
        return redirect(url_for('category_dashboard'))
    result = users_collection.update_one(
        {'username': username},
        {'$pull': {'categories': category}}
    )
    if result.modified_count > 0:
        flash(f"User '{username}' removed from category '{category}'")
    else:
        flash(f"User '{username}' is not in category '{category}' or does not exist")
    if session.get('role') == 'categoryadmin':
        return redirect(url_for('category_dashboard'))
    else:
        return redirect(url_for('approve_requests'))

@app.route('/register_admin', methods=['GET', 'POST'])
def register_admin():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        category_from_dropdown = request.form.get('category', '').strip()
        new_category = request.form.get('new_category', '').strip()
        if not category_from_dropdown and not new_category:
            flash("Please select an existing category or enter a new category.")
            return redirect(url_for('register_admin'))
        if new_category:
            existing_category = categories_collection.find_one({'name': new_category})
            if not existing_category:
                categories_collection.insert_one({'name': new_category})
        category = new_category if new_category else category_from_dropdown
        requests_collection.insert_one({
            'username': username,
            'password': password,
            'role': 'categoryadmin',
            'category': category
        })
        flash('Admin registration request sent to Super Admin for approval.')
        return redirect(url_for('login'))
    categories = list(categories_collection.find())
    return render_template('register_admin.html', categories=categories)

@app.route('/manage_category')
def manage_category():
    if 'username' not in session or session.get('role') != 'superadmin':
        flash('Only Super Admin can view this page.')
        return redirect(url_for('login'))
    categories = list(categories_collection.find())
    return render_template('manage_category.html', categories=categories)

@app.route('/add_category', methods=['POST'])
def add_category():
    if 'username' not in session or session.get('role') != 'superadmin':
        flash('Only Super Admin can add categories.')
        return redirect(url_for('login'))
    category_name = request.form['category_name']
    if not categories_collection.find_one({'name': category_name}):
        categories_collection.insert_one({'name': category_name})
        flash('Category added successfully.')
    else:
        flash('Category already exists.')
    return redirect(url_for('manage_category'))

@app.route('/delete_category', methods=['POST'])
def delete_category():
    if 'username' not in session or session.get('role') != 'superadmin':
        flash('Only Super Admin can delete categories.')
        return redirect(url_for('login'))
    category_name = request.form['delete_category_name']
    category = categories_collection.find_one({'name': category_name})
    if category:
        categories_collection.delete_one({'name': category_name})
        flash('Category deleted successfully.')
    else:
        flash('Category not found.')
    return redirect(url_for('manage_category'))

@app.route('/approve_requests')
def approve_requests():
    if 'username' not in session or session.get('role') != 'superadmin':
        return redirect(url_for('login'))
    requests_list = list(requests_collection.find())
    categories = list(categories_collection.find())
    users = list(users_collection.find())
    category_admins = list(users_collection.find({'role': 'categoryadmin'}))
    category_users = {}
    for admin in category_admins:
        category_name = admin.get('category')
        sub_users = list(users_collection.find({'categories': {'$in': [category_name]}}))
        category_users[category_name] = sub_users
    return render_template('approve_requests.html',
                           requests=requests_list,
                           categories=categories,
                           users=users,
                           category_admins=category_admins,
                           category_users=category_users)


@app.route('/approve/<request_id>', methods=['POST'])
def approve(request_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    request_data = requests_collection.find_one({'_id': ObjectId(request_id)})
    if not request_data:
        flash("Request not found")
        return redirect(url_for('approve_requests'))
    if session.get('role') == 'superadmin':
        if 'categories' in request_data:
            user_doc = {
                'username': request_data['username'],
                'password': request_data['password'],
                'role': request_data['role'],
                'categories': request_data['categories']
            }
        else:
            user_doc = {
                'username': request_data['username'],
                'password': request_data['password'],
                'role': request_data['role'],
                'category': request_data['category']
            }
        users_collection.insert_one(user_doc)
        requests_collection.delete_one({'_id': ObjectId(request_id)})
        flash('User approved successfully')
        return redirect(url_for('approve_requests'))
    if session.get('role') == 'categoryadmin':
        admin_category = session.get('category')
        if 'categories' in request_data:
            if admin_category not in request_data['categories']:
                flash('Permission denied. You cannot approve requests for categories you are not responsible for.')
                return redirect(url_for('category_dashboard'))
            approvals = request_data.get('approvals', [])
            if admin_category not in approvals:
                approvals.append(admin_category)
                requests_collection.update_one(
                    {'_id': ObjectId(request_id)},
                    {'$set': {'approvals': approvals}}
                )
                flash(f"Approval recorded for category {admin_category}.")
            else:
                flash(f"You have already approved this request for category {admin_category}.")
            if set(request_data['categories']) == set(approvals):
                user_doc = {
                    'username': request_data['username'],
                    'password': request_data['password'],
                    'role': request_data['role'],
                    'categories': request_data['categories']
                }
                users_collection.insert_one(user_doc)
                requests_collection.delete_one({'_id': ObjectId(request_id)})
                flash("User fully approved and registered.")
                return redirect(url_for('category_dashboard'))
            else:
                flash("Request approval recorded. Awaiting approvals for other categories.")
                return redirect(url_for('category_dashboard'))
        else:
            if request_data.get('category') != admin_category:
                flash('Permission denied. You cannot approve requests for other categories.')
                return redirect(url_for('category_dashboard'))
            user_doc = {
                'username': request_data['username'],
                'password': request_data['password'],
                'role': request_data['role'],
                'category': request_data['category']
            }
            users_collection.insert_one(user_doc)
            requests_collection.delete_one({'_id': ObjectId(request_id)})
            flash("User approved successfully")
            return redirect(url_for('category_dashboard'))

@app.route('/reject/<request_id>', methods=['POST'])
def reject(request_id):
    if 'username' not in session:
        flash('You need to log in to perform this action.')
        return redirect(url_for('login'))
    request_data = requests_collection.find_one({'_id': ObjectId(request_id)})
    if not request_data:
        flash("Request not found")
        return redirect(url_for('category_dashboard'))
    if 'categories' in request_data:
        if session.get('role') == 'categoryadmin' and session.get('category') not in request_data['categories']:
            flash('Permission denied. You cannot reject requests for categories you are not responsible for.')
            return redirect(url_for('category_dashboard'))
    else:
        if session.get('role') == 'categoryadmin' and request_data['category'] != session.get('category'):
            flash('Permission denied. You cannot reject requests for other categories.')
            return redirect(url_for('category_dashboard'))
    requests_collection.delete_one({'_id': ObjectId(request_id)})
    flash('User rejected successfully')
    if session.get('role') == 'categoryadmin':
        return redirect(url_for('category_dashboard'))
    else:
        return redirect(url_for('approve_requests'))

@app.route('/category_dashboard', methods=['GET', 'POST'])
def category_dashboard():
    if 'username' not in session or session.get('role') != 'categoryadmin':
        return redirect(url_for('login'))
    admin_category = session.get('category')
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'upload':
            images = request.files.getlist('images')
            if images:
                for image in images:
                    if image:
                        file_id = fs.put(image.read(), filename=image.filename)
                        categories_collection.update_one(
                            {'name': admin_category},
                            {'$push': {'images': {'file_id': file_id, 'filename': image.filename}}},
                            upsert=True
                        )
                flash('Image(s) uploaded successfully')
            else:
                flash('No image selected for upload')
        elif action == 'remove':
            file_id_str = request.form.get('file_id')
            if file_id_str:
                try:
                    fs.delete(ObjectId(file_id_str))
                    categories_collection.update_one(
                        {'name': admin_category},
                        {'$pull': {'images': {'file_id': ObjectId(file_id_str)}}}
                    )
                    flash('Image removed successfully')
                except Exception as e:
                    flash(f'Error removing image: {str(e)}')
            else:
                flash('No image selected for removal')
        return redirect(url_for('category_dashboard'))
    
    category_data = categories_collection.find_one({'name': admin_category})
    images = category_data.get('images', []) if category_data else []
    pending_requests = list(requests_collection.find({
        '$or': [
            {'categories': admin_category},
            {'category': admin_category}
        ]
    }))
    sub_users = list(users_collection.find({
        'role': 'user',
        '$or': [
            {'categories': admin_category},
            {'category': admin_category}
        ]
    }))
    search_query = request.args.get('search_sub_user', '')
    if search_query:
        sub_users = list(users_collection.find({
            'role': 'user',
            '$or': [
                {'categories': admin_category},
                {'category': admin_category}
            ],
            'username': {'$regex': search_query, '$options': 'i'}
        }))
    return render_template('category_dashboard.html',
                           images=images,
                           requests=pending_requests,
                           sub_users=sub_users)



@app.route('/report')
def report():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = users_collection.find_one({'username': session['username']})
    if not user:
        return redirect(url_for('login'))

    reports = []
    try:
        raw_reports = reports_collection.find().sort('generated_at', -1)

        for report in raw_reports:
            if 'matches' in report:
                for match in report['matches']:
                    if 'Image' in match:
                        # Convert to string explicitly
                        file_id_str = str(match['Image'])
                        match['image_url'] = url_for('get_image', file_id=file_id_str)
            reports.append(report)

    except Exception as e:
        flash(f"Error loading reports: {str(e)}")

    return render_template('report.html', reports=reports)


@app.route('/get_image/<file_id>')
def get_image(file_id):
    try:
        # Validate ObjectId format
        if not ObjectId.is_valid(file_id):
            raise InvalidId("Invalid file ID format")

        file_obj = fs.get(ObjectId(file_id))
        return send_file(
            io.BytesIO(file_obj.read()),
            mimetype='image/jpeg',
            download_name=file_obj.filename
        )

    except InvalidId:
        flash("Invalid image ID format")
        return redirect(url_for('report'))
    except Exception as e:
        print(f"Error retrieving image {file_id}: {str(e)}")  # Log the error
        flash("Image not found")
        return redirect(url_for('report'))


@app.route('/export_pdf/<report_id>')
def export_pdf(report_id):
    try:
        # Get report data
        report = reports_collection.find_one({'_id': ObjectId(report_id)})
        if not report:
            flash('Report not found')
            return redirect(url_for('report'))

        # Create PDF buffer
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)

        # Create styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=12,
            alignment=1  # Center aligned
        )

        elements = []

        # Add title
        elements.append(Paragraph("Face Recognition Report", title_style))

        # Add report details
        details = [
            ['Category:', report['category']],
            ['Date:', report['generated_at'].strftime('%Y-%m-%d %H:%M:%S')],
            ['Confidence:', report['matches'][0]['Confidence']]
        ]

        # Create table
        table = Table(details, colWidths=[100, 400])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 24))

        # Add image if available
        if 'matches' in report and report['matches'] and 'Image' in report['matches'][0]:
            try:
                image_file = fs.get(report['matches'][0]['Image'])
                img = Image(BytesIO(image_file.read()), width=400, height=300)
                elements.append(Paragraph("Detection Snapshot:", styles['Heading2']))
                elements.append(img)
            except Exception as e:
                print(f"Error loading image: {str(e)}")

        # Build PDF
        doc.build(elements)

        # Prepare response
        buffer.seek(0)
        timestamp = report['generated_at'].strftime('%Y%m%d_%H%M%S')
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Report_{report['category']}_{timestamp}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        print(f"PDF generation error: {str(e)}")
        flash('Error generating PDF')
        return redirect(url_for('report'))
@app.route('/search_sub_users', methods=['GET'])
def search_sub_users():
    if 'username' not in session or session.get('role') != 'categoryadmin':
        return '', 403
    admin_category = session.get('category')
    search_query = request.args.get('search_sub_user', '')
    sub_users = list(users_collection.find({
        'role': 'user',
        '$or': [
            {'categories': admin_category},
            {'category': admin_category}
        ],
        'username': {'$regex': search_query, '$options': 'i'}
    }))
    return render_template('sub_users_table.html', sub_users=sub_users)


if __name__ == '__main__':
    app.run(debug=True)