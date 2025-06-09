# Streamlit App
import streamlit as st
import numpy as np
import pandas as pd
from openai import OpenAI
import re
from fpdf import FPDF
import os 
import io

st.title("Amazon Seller Central Profitability Report")

st.write("""
This app helps you analyze your Amazon Seller Central transaction report.
Upload your transaction in  CSV format file to get started.
""")

uploaded_file = st.file_uploader("Upload your Amazon Transaction CSV file", type="csv")

df = None

if uploaded_file is not None:
    try:
        # Read the CSV file
        df = pd.read_csv(uploaded_file)
        st.success("File uploaded successfully!")
        st.write("Original Data Preview:")
        st.dataframe(df.head())

        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df['quantity'] = pd.to_numeric(df['quantity-purchased'], errors='coerce')
        grouped_df = df.groupby('amount-type')['amount'].sum().reset_index()

        net_revenue_components = ['ItemPrice', 'ItemWithheldTax', 'Promotion']
        net_revenue_df = grouped_df[grouped_df['amount-type'].isin(net_revenue_components)]
        net_revenue = net_revenue_df['amount'].sum()

        grouped_df.loc[~grouped_df['amount-type'].isin(net_revenue_components), '% of net revenue'] = round(abs(grouped_df['amount']) / net_revenue * 100, 2)
        tax_collected_by_amazon = abs(grouped_df[grouped_df['amount-type']=='ItemWithheldTax'].reset_index().iloc[0]['amount'])

        # Use errors='coerce' to turn unparseable dates into NaT (Not a Time)
        df['settlement-start-date'] = pd.to_datetime(df['settlement-start-date'], errors='coerce')
        df['settlement-end-date'] = pd.to_datetime(df['settlement-end-date'], errors='coerce')

        lowest_start_date = df['settlement-start-date'].min()
        highest_end_date = df['settlement-end-date'].max()

        # Format the dates to "Month, Year"
        # Check if dates are NaT before formatting
        if pd.isna(lowest_start_date):
            formatted_lowest_start_date = "N/A"
        else:
            formatted_lowest_start_date = lowest_start_date.strftime('%B, %Y')

        if pd.isna(highest_end_date):
            formatted_highest_end_date = "N/A"
        else:
            formatted_highest_end_date = highest_end_date.strftime('%B, %Y')

        itemFees = df.groupby(['amount-description', 'amount-type'])['amount'].sum().reset_index()
        itemFees = itemFees[itemFees['amount-type']=='ItemFees'].reset_index(drop=True)
        itemFees['% of net revenue'] = round(abs(itemFees['amount']) / net_revenue * 100, 2)
        
        product_details_qty = df[(df['amount-type']=='ItemPrice') & (df['amount-description']=='Principal')]
        product_details_amount = df[df['amount-type'].isin(net_revenue_components)]

        #     amount=('amount', 'sum'),
        product_qty = product_details_qty.groupby('sku').agg(
            quantity=('quantity', 'sum')
        ).reset_index()

        product_revenue = product_details_amount.groupby('sku').agg(
            revenue=('amount', 'sum')
        ).reset_index()

        product_details = pd.merge(product_qty, product_revenue, on='sku', how='inner').sort_values(by='revenue', ascending=False).reset_index(drop=True)

        st.write("### Product Details (Revenue and Quantity per SKU)")
        st.dataframe(product_details)

        # --- 2. Ask for unit price of every SKU ---
        st.write("### Enter Unit Cost (COGS) for each SKU")
        st.write("Please provide the cost for *one unit* of each product listed below.")

        # Create a dictionary to store user input unit prices
        unit_prices = {}
        skus_to_get_price = product_details['sku'].dropna().unique() # Get unique, non-null SKUs

        # Use st.data_editor to allow inline editing of unit prices
        # Create a DataFrame for editing
        unit_price_editor_df = pd.DataFrame({
            'sku': skus_to_get_price,
            'Unit Cost': [0.0] * len(skus_to_get_price) # Initialize with 0 or load previous if state is used
        })

        edited_df = st.data_editor(unit_price_editor_df, num_rows="dynamic", key="unit_price_editor")

        profit = 0

        # Convert edited data back to dictionary for calculations
        if not edited_df.empty:
            try:
                # Ensure 'Unit Cost' is numeric, coerce errors to NaN
                edited_df['Unit Cost'] = pd.to_numeric(edited_df['Unit Cost'], errors='coerce')
                # Drop rows where Unit Cost is NaN (couldn't be converted to number) or SKU is NaN
                edited_df = edited_df.dropna(subset=['sku', 'Unit Cost'])
                final_product_df = pd.merge(product_details, edited_df, on='sku', how='inner')
                final_product_df['total_cost'] = final_product_df['Unit Cost']*final_product_df['quantity']
                total_cost = final_product_df['total_cost'].sum()
                st.write(f"Total Cost of Goods Sold (COGS): ${total_cost:,.2f}")

                profit = (net_revenue - total_cost)
                st.write(f"Estimated Profit: ${profit:,.2f}")
            except Exception as e:
                st.error(f"Error processing unit prices: {e}. Please ensure Unit Cost is a valid number.")
                unit_prices = {} # Reset unit prices if there's an error

        top_products = product_details.head(5)
        top_product_skus = top_products['sku']
        top_product_skus_list = top_product_skus.tolist()

        top_products_df = df[df['sku'].isin(top_product_skus_list)].copy()
        top_products_promotion_cost = top_products_df[top_products_df['amount-type'].isin(['Promotion'])].copy()
        top_products_commission_shipping = top_products_df[(top_products_df['amount-type']=='ItemFees') & (top_products_df['amount-description'].isin(['Commission', 'FBAPerUnitFulfillmentFee']))].copy()


        top_products_promotion_costs_grouped = top_products_promotion_cost.groupby(['sku', 'amount-type'])['amount'].sum().reset_index()
        top_products_commission_shipping_grouped = top_products_commission_shipping.groupby(['sku', 'amount-description'])['amount'].sum().reset_index()

        top_products_costs_grouped = pd.concat([top_products_promotion_costs_grouped, top_products_commission_shipping_grouped])
        top_products_costs_grouped['description'] = np.where(
            top_products_costs_grouped['amount-type'].notna(),
            top_products_costs_grouped['amount-type'],
            top_products_costs_grouped['amount-description']
        )
        # Drop the original 'amount-type' and 'amount-description' columns
        top_products_costs_grouped = top_products_costs_grouped.drop(columns=['amount-type', 'amount-description'])

        top_products_costs_pivot = top_products_costs_grouped.pivot_table(index='sku', columns='description', values='amount', fill_value=0).reset_index()
        top_products_summary = pd.merge(top_products_costs_pivot, top_products[['sku', 'quantity','revenue']], on='sku', how='left')

        relevant_description = ['Promotion','Commission', 'FBAPerUnitFulfillmentFee']

        # Calculate the percentage from revenue for each cost category
        for cost_type in relevant_description:
            # Check if the cost type column exists in the pivoted DataFrame
            if cost_type in top_products_summary.columns:
                # Use abs() because these amounts are typically negative
                top_products_summary[f'% of {cost_type} from Product Revenue'] = round(abs(top_products_summary[cost_type]) / top_products_summary['revenue'] * 100, 2)
            else:
                # If the column doesn't exist, set the percentage to 0 or NaN
                top_products_summary[f'% of {cost_type} from Revenue'] = 0.0 # Or np.nan if you prefer
            top_products_summary = top_products_summary.rename(columns={'% of FBAPerUnitFulfillmentFee from Product Revenue': '% of shipping cost from Product Revenue'})

        top_products_summary_part_1 = top_products_summary[['sku','revenue','quantity','Commission','FBAPerUnitFulfillmentFee','Promotion']].sort_values(by='revenue', ascending=False).reset_index(drop=True)
        top_products_summary_part_1 = top_products_summary_part_1.rename(columns={'Commission': 'Commission cost', 'FBAPerUnitFulfillmentFee': 'Shipping cost', 'Promotion': 'Promotion cost'})
        top_products_summary_part_2 = top_products_summary[['sku','% of Promotion from Product Revenue','% of Commission from Product Revenue','% of shipping cost from Product Revenue']]
        top_products_summary_part_2 = top_products_summary_part_2.rename(columns={'% of Promotion from Product Revenue': 'Promotion cost %', '% of Commission from Product Revenue': 'Commission cost %', '% of shipping cost from Product Revenue': 'Shipping cost %'})

        fba_fee_df = itemFees[itemFees['amount-description'] == 'Commission']

        if not fba_fee_df.empty:
            commission_collected_by_amazon = abs(fba_fee_df['amount'].iloc[0])
            commission_collected_by_amazon_pct = abs(fba_fee_df['% of net revenue'].iloc[0])
        else:
            tax_collected_by_amazon = 0

        shipping_cost = abs(itemFees[itemFees['amount-description']=='FBAPerUnitFulfillmentFee'].reset_index().iloc[0]['amount'])
        advertising_cost = abs(grouped_df[grouped_df['amount-type']=='Cost of Advertising'].reset_index().iloc[0]['amount'])
        promotion_expense = abs(grouped_df[grouped_df['amount-type']=='Promotion'].reset_index().iloc[0]['amount'])

        shipping_cost_to_revenue = shipping_cost*100/net_revenue
        advertising_cost_to_revenue = advertising_cost*100/net_revenue
        promotion_expense_to_revenue = promotion_expense*100/net_revenue

        API_KEY = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=API_KEY)

        def call_chat(system_prompt, prompt, model="gpt-4o-mini-2024-07-18"):

            res = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ]
            )
            try:
              return res.choices[0].message.content
            except Exception as e:
              raise e

        system_prompt = '''
          You are a business analyst and you analyze business and create summary of that business
        '''

        user_prompt = '''
        Generate a detailed, professional financial analysis report for a brand owner, based on transaction data from their Amazon Seller Central account. The report should offer clear insights into their financial standing, including gross margin, and provide an expert opinion on major expense types relative to industry benchmarks. It should conclude with actionable recommendations for improving profitability.

        Report Structure and Content Guidelines:

        1. Report Overview

        Date Range of Analysis: Clearly state the analysis period: {start_date} to {end_date}.
        Data Coverage: Specify that the analysis covers the approximate how many months of financial data.
        Total Net Revenue: Present the total net revenue for the entire period: ${net_revenue}.
        2. Revenue and Expense Summary

        Introduce the following table as a comprehensive overview of revenues and various expense types, clarifying that the sum of these figures represents the exact amount received in the bank account, ensuring accuracy.
        Present the table: {grouped_df}
        Explanation of Major Expense Types:
        Item Price: Define as gross revenue.
        Net Revenue: Explain as Item Price + Item Withheld Tax + Promotion.
        Cost of Advertising: Detail the advertising expenditure.
        Item Fees: Explain as a cumulative category of various Amazon-related fees.
        3. Key Expense Ratios

        Advertising Cost to Revenue: {advertising_cost_to_revenue}%
        Promotions Cost to Revenue: {promotion_expense_to_revenue}%
        Taxes Collected by Amazon: ${tax_collected_by_amazon}
        4. Detailed Item Fees Breakdown

        Introduce the following table as a breakdown of the 'Item Fees' category, explaining how each component contributes to the overall item fees.
        Present the table: {itemfees}
        Component Percentages of Revenue:
        Shipping and Handling: {shipping_cost_to_revenue}%
        Amazon Commission: ${commission_collected_by_amazon} ({commission_collected_by_amazon_pct}%)
        5. Gross Profit Calculation

        Emphasize that gross profit is calculated by deducting Cost of Goods Sold (COGS) and any other off-Amazon expenses from the total amount received in the bank account for the period.
        Gross Profit for the business: {profit}

        6. Industry Standard Benchmarking and Analysis

        Provide an expert commentary on whether the major expense types are healthy, low, or indicate potential issues, referencing the following industry standards:
        Shipping Expense:
        < 30% of revenue: Acceptable
        ~25% of revenue: Healthy
        <= 20% of revenue: Very Good
        Advertising Spend:
        < 10% of revenue: Maintenance
        ~15% of revenue: Moderate Growth
        ~20% of revenue: Aggressive Growth (depends on business size)
        Promotion Expense:
        < 30% of revenue: Acceptable
        ~25% of revenue: Healthy
        <= 20% of revenue: Very Good
        7. Item-Level Performance Analysis (Top 5 Products)

        Introduction: Provide an item-by-item analysis for the top five products by revenue.
        Product Details: {top_products_summary_part_1}
        Cost Percentages of Revenue: {top_products_summary_part_2}
        For all products, indicate whether it is performing well or requires adjustments to improve profitability.
        8. Overall Insights and Actionable Recommendations

        Clearly identify areas where costs can be reduced to enhance overall profitability.
        Recommend increasing ad spend only if current expenditure is significantly below industry standards or growth objectives.
        Maintain a primary focus on improving profitability rather than solely emphasizing growth.
        Formatting Guidance for PDF Conversion:

        Use clear, consistent heading formatting (e.g., Section Title, Subsection Title).
        Output only the report text, suitable for direct PDF conversion, without any additional conversational elements or prompt instructions.
        Format tables cleanly with clear headers and aligned data, ensuring they render correctly in a PDF document.

        '''

        # Download and register Unicode font
        # Ensure fonts are installed
        # get_ipython().system('apt-get update')
        # get_ipython().system('apt-get install -y fonts-dejavu')

        # Verify the font file exists after installation
        unicode_font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        unicode_bold_font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' # Define path for the bold font as well

        if not os.path.exists(unicode_font_path):
            print(f"Error: Regular font file not found at {unicode_font_path}. Please check the installation.")
            # Handle the error, perhaps by exiting or using a different font
            # For now, we'll let it proceed to see the error explicitly if it still exists
            pass # Or exit() or raise an error

        if not os.path.exists(unicode_bold_font_path):
            print(f"Error: Bold font file not found at {unicode_bold_font_path}. Please check the installation.")
            pass # Or exit() or raise an error


        # Helper: Render text with **bold** formatting
        def render_bold_text(pdf, text):
            parts = re.split(r'(\*\*.*?\*\*)', text)
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    pdf.set_font("Helvetica", "B", 10)
                    pdf.write(6, part[2:-2])
                else:
                    pdf.set_font("Helvetica", "", 10)
                    pdf.write(6, part)
            pdf.ln(7)

        # Helper: Draw table with proper alignment - LAST ATTEMPT for older FPDF
        def draw_table(pdf, data, col_widths, line_height=8):
            pdf.ln(5)

            # Calculate proper column widths if not set (same as before)
            if not col_widths or all(w == 0 for w in col_widths):
                col_widths = []
                for col_idx in range(len(data[0]) if data else 0):
                    max_width = 0
                    for row in data:
                        if col_idx < len(row):
                            cell_width = pdf.get_string_width(str(row[col_idx])) + 6
                            max_width = max(max_width, cell_width)
                    col_widths.append(max_width)

            # Ensure total width doesn't exceed page width (same as before)
            page_width = pdf.w - 2 * pdf.l_margin
            total_width = sum(col_widths)
            if total_width > page_width:
                scale_factor = page_width / total_width
                col_widths = [w * scale_factor for w in col_widths]

            for row_index, row in enumerate(data):
                # Calculate the maximum height needed for this row
                calculated_max_row_height = line_height

                if row_index == 0:
                    pdf.set_font("Helvetica", "B", 10)
                else:
                    pdf.set_font("Helvetica", "", 10)

                # First pass: Determine max height for the row
                for i, text in enumerate(row):
                    if i < len(col_widths) and col_widths[i] > 0:
                        text_str = str(text)
                        cell_width = col_widths[i]

                        text_width = pdf.get_string_width(text_str)
                        effective_cell_width = cell_width - pdf.c_margin * 2

                        if effective_cell_width > 0:
                            estimated_lines = (text_width / effective_cell_width) + 0.999999
                            estimated_cell_height = estimated_lines * line_height
                            calculated_max_row_height = max(calculated_max_row_height, estimated_cell_height)
                        else:
                            calculated_max_row_height = max(calculated_max_row_height, line_height)

                final_row_height = calculated_max_row_height + 2 # Add buffer

                initial_y = pdf.get_y() # Store current Y position

                # Page break check
                if initial_y + final_row_height > pdf.h - pdf.b_margin:
                    pdf.add_page()
                    initial_y = pdf.get_y()

                current_x = pdf.l_margin

                # Second pass: Draw each cell with the determined max height
                for i, text in enumerate(row):
                    if i < len(col_widths):
                        if row_index == 0:
                            pdf.set_font("Helvetica", "B", 10)
                        else:
                            pdf.set_font("Helvetica", "", 10)


                        # Set position for the current cell
                        pdf.set_xy(current_x, initial_y)

                        # Draw the cell. multi_cell will still advance the cursor's Y internally.
                        pdf.multi_cell(col_widths[i], final_row_height, str(text), border=1, align='L')

                        # After multi_cell, the Y cursor has advanced.
                        # To draw the next cell in the same row, we explicitly move X.
                        current_x += col_widths[i]

                # After drawing all cells in the row, explicitly move the Y cursor
                # to the beginning of the next row. This ensures uniform row spacing.
                pdf.set_y(initial_y + final_row_height)

        # Main: Add content to PDF
        def add_content_to_pdf(pdf, text):
            lines = text.split('\n')
            in_table = False
            table_data = []
            col_widths = []

            for line in lines:
                line = line.replace('•', '-')  # Clean bullets

                # Headers
                if line.strip().startswith('#'):
                    if in_table:
                        draw_table(pdf, table_data, col_widths)
                        in_table = False
                        table_data, col_widths = [], []

                    header_level = len(re.match(r'#+', line).group(0))
                    header_text = line.replace('#', '').strip()
                    size = {1: 18, 2: 14}.get(header_level, 12)
                    pdf.set_font("Helvetica", "B", size)

                    pdf.ln(8)
                    pdf.cell(0, 10, header_text, 0, 1)
                    pdf.ln(3)
                    continue

                # Bulleted list
                if line.strip().startswith('-'):
                    if in_table:
                        draw_table(pdf, table_data, col_widths)
                        in_table = False
                        table_data, col_widths = [], []
                    pdf.set_font("Helvetica", "", 10)
                    render_bold_text(pdf, "- " + line.strip()[1:].strip())
                    continue

                # Numbered list
                if re.match(r'^\d+\.', line.strip()):
                    if in_table:
                        draw_table(pdf, table_data, col_widths)
                        in_table = False
                        table_data, col_widths = [], []
                    pdf.set_font("Helvetica", "", 10)
                    render_bold_text(pdf, line.strip())
                    continue

                # Table detection and processing
                if line.strip().startswith('|') and line.strip().endswith('|'):
                    # Skip markdown table separator lines
                    if all(c in ('-', ':', '|', ' ') for c in line.strip()):
                        continue

                    if not in_table:
                        in_table = True
                        table_data = []
                        col_widths = []

                    # Parse table row
                    row = [c.strip() for c in line.strip('|').split('|')]
                    table_data.append(row)
                    continue

                # If we were in a table and now we're not, render the table
                if in_table and line.strip() and not line.strip().startswith('|'):
                    draw_table(pdf, table_data, col_widths)
                    in_table = False
                    table_data, col_widths = [], []

                # Paragraph
                if line.strip():
                    if not in_table:  # Only render if not in table
                        render_bold_text(pdf, line.strip())
                else:
                    if not in_table:
                        pdf.ln(5)

            # Render any remaining table
            if in_table:
                draw_table(pdf, table_data, col_widths)

        # --- Calculate button ---
        if st.button("Generate Report"):
            with st.spinner("Generating and formatting report..."):
                business_summary = call_chat(
                    system_prompt=system_prompt,
                    prompt=user_prompt.format(start_date=formatted_lowest_start_date, end_date=formatted_highest_end_date, net_revenue=net_revenue, grouped_df=grouped_df.to_markdown(index=False), advertising_cost_to_revenue=advertising_cost_to_revenue,
                                  promotion_expense_to_revenue=promotion_expense_to_revenue, tax_collected_by_amazon=tax_collected_by_amazon, itemfees=itemFees.to_markdown(index=False),
                                  shipping_cost_to_revenue=shipping_cost_to_revenue,
                                  commission_collected_by_amazon=commission_collected_by_amazon, commission_collected_by_amazon_pct=commission_collected_by_amazon_pct,
                                  top_products_summary_part_1=top_products_summary_part_1.to_markdown(index=False), top_products_summary_part_2=top_products_summary_part_2.to_markdown(index=False), profit = profit )
                    )

                # Create and configure PDF
                pdf = FPDF()
                pdf.add_page()
                pdf.set_auto_page_break(auto=True, margin=15)
                pdf.set_font("Helvetica", "", 10) # Fallback to a standard font

                # Add content
                add_content_to_pdf(pdf, business_summary.strip())

                pdf_bytes = pdf.output(dest='S').encode('utf-8')

            st.write(business_summary)

            st.download_button(
                label="Download Full Report (PDF)", # Updated label for clarity
                data=pdf_bytes,
                file_name='business_report.pdf',
                mime='application/pdf'
            )

    except Exception as e:
        st.error(f"An error occurred: {e}")
        st.write("Please ensure you have uploaded the correct Amazon Transaction CSV file.")