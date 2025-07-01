function addReportsToDropdown() {
  // Your JSON data (replace with your full JSON or import if needed)
  const jsonData = [
    {
      "employee_id": "2379",
      "employee_name": "Hanifah Abolais"
    },
    {
      "employee_id": "2345",
      "employee_name": "Byron Andino"
    },
    {
      "employee_id": "2227",
      "employee_name": "Dean Antonio"
    }
    // Add more employees as needed
  ];

  // Format as 'Name (ID)'
  const dropdownOptions = jsonData.map(e => `${e.employee_name} (${e.employee_id})`);

  // Get active sheet
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

  // Find the Reports To column (adjust index if needed)
  // For example, L = 12 (A=1)
  const col = 12;

  // Remove existing validation if any
  const range = sheet.getRange(2, col, sheet.getLastRow());
  range.clearDataValidations();

  // Add the new drop-down list
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(dropdownOptions, true)
    .setAllowInvalid(false)
    .build();

  range.setDataValidation(rule);

  Logger.log('Reports To drop-down added successfully!');
}
