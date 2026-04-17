Absolutely — I’ve revised the document so it clearly refers to **Databricks Apps** and **Databricks App cost optimization**, not generic “applications.” The language now aligns with Databricks Apps lifecycle, cost controls, and governance.

---

# **Databricks Apps Stop/Start Monitoring and Auto‑Deletion Policy**

## **1. Overview**

The Databricks Apps Stop/Start Monitoring capability provides an automated and governed approach to managing Databricks App runtime behavior. Its purpose is to ensure compute and storage resources are used efficiently while still supporting workloads that require continuous availability.

This framework enforces a default shutdown policy and introduces structured exception mechanisms for both new and legacy Databricks Apps. Apps that do not require continuous operation are automatically stopped during each monitoring cycle to support cost optimization and operational hygiene.

As part of the broader cloud cost‑optimization strategy, this capability has been enhanced to include **automatic deletion of inactive Databricks Apps**. Any Databricks App that remains stopped and unused for more than **30 consecutive days** will be automatically deleted unless it has an approved exception.

This enhancement helps:

- Reduce Databricks App‑related cloud costs  
- Improve resource utilization  
- Minimize operational overhead  
- Maintain a clean and accurate Databricks App inventory  
- Remove stale or abandoned workloads  

---

## **2. Default Shutdown Policy**

All Databricks Apps are subject to a default stop action during each monitoring cycle.

This ensures:

- Apps not requiring continuous operation do not remain running unnecessarily  
- Cost optimization and operational discipline are consistently applied  
- Inactive Databricks Apps are automatically transitioned to a stopped state  

---

## **3. Exception Mechanism for DAB‑Provisioned Databricks Apps**

Databricks Apps deployed using **Databricks Asset Bundles (DAB)** can be exempted from the default shutdown policy.

To qualify:

- A designated **exception tag** must be included during provisioning  
- When present, the monitoring system recognizes the Databricks App as requiring **24/7/365 availability**  
- These apps are excluded from the automated stop workflow  

This tag‑based approach provides a standardized, auditable, and transparent method for requesting continuous runtime while maintaining governance controls.

---

## **4. Exception Handling for Pre‑Existing Databricks Apps**

For Databricks Apps created **before** the introduction of this feature, exceptions are managed through a centralized **exception JSON file**.

- If a Databricks App name appears in this file, it is excluded from the stop process  
- This ensures backward compatibility and prevents disruption to legacy workloads  
- Platform teams gain improved visibility and control over older Databricks Apps that still require continuous availability  

---

## **5. Auto‑Deletion of Stopped Databricks Apps After 30 Days**

As an extension of the monitoring framework, Databricks Apps that remain in a **stopped state for more than 30 consecutive days** will be automatically scheduled for deletion.

### **Deletion applies only when all conditions are met:**

- The Databricks App has been stopped by the monitoring process  
- It remains inactive for more than 30 days  
- It does **not** have an approved exception for retention  

### **Benefits of the deletion policy:**

- Reduced Databricks App‑related cloud costs  
- Stronger resource governance  
- Cleaner Databricks App inventory  
- Lower operational complexity  
- Removal of stale or abandoned workloads  

---

## **6. Exception Handling for Deletion**

Databricks Apps requiring retention beyond the 30‑day inactivity threshold must have a valid justification, such as:

- Business requirements  
- Compliance or audit needs  
- Operational dependencies  

### **Exception requests must include:**

- Business justification  
- Databricks App ownership details  
- Expected retention period  
- Approval from designated governance owners  

This ensures flexibility for critical workloads while maintaining strong governance and lifecycle management.

---

## **7. Conclusion**

The enhanced Databricks Apps Stop/Start Monitoring and Auto‑Deletion framework strengthens cloud resource management by combining:

- Automated stopping of inactive Databricks Apps  
- Exception‑based support for continuous runtime  
- Lifecycle‑driven cleanup of unused Databricks Apps  

By introducing automatic deletion after 30 days of inactivity, the platform improves cost efficiency, operational discipline, and alignment with enterprise cloud governance standards—while still allowing controlled exceptions for business‑critical Databricks Apps.

---

If you want, I can also produce a **cost‑focused executive summary**, **policy one‑pager**, or **slide‑ready version**.
